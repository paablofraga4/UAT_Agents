"""Multi-agent ReAct workflow, driven by a plain async loop (no graph
framework — see the driver section at the bottom for why).

ReAct loop:

    observer → pilot ──act──► executor → observer (cycle)
                  │
                  ├──done────► reporter → END
                  └──give_up─► reporter → END

The Pilot decides ONE action at a time given the live page context and the
history of prior steps. This is far more robust on real, dynamic SPAs (LinkedIn,
Salesforce, Slack…) than a one-shot multi-step plan.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict

from app import knowledge
from app.agents import llm, prompts
from app.agents.events import bus
from app.config import settings
from app.browser.runner import ActionExecutionError, execute_action, observe
from app.browser.session_broker import broker
from app.models.schemas import (
    Action,
    AssertFieldValueAction,
    AssertTextAction,
    AssertUrlContainsAction,
    ClickElementAction,
    ClickRoleAction,
    ClickTextAction,
    ExtractContextAction,
    FillLabelAction,
    FillPlaceholderAction,
    GotoAction,
    PageContext,
    PressKeyAction,
    ScreenshotAction,
    SelectOptionAction,
    StepResult,
    StepStatus,
    TaskStatus,
    ScrollAction,
    TypeTextAction,
    UploadFileAction,
    WaitAction,
    WaitForTextAction,
)
from app.storage import db
from app.storage.evidence import relative_to_evidence, task_dir, write_json, write_text


ACTION_REGISTRY = {
    "goto": GotoAction,
    "click_text": ClickTextAction,
    "click_role": ClickRoleAction,
    "click_element": ClickElementAction,
    "fill_label": FillLabelAction,
    "fill_placeholder": FillPlaceholderAction,
    "type_text": TypeTextAction,
    "select_option": SelectOptionAction,
    "press_key": PressKeyAction,
    "wait": WaitAction,
    "wait_for_text": WaitForTextAction,
    "screenshot": ScreenshotAction,
    "assert_text": AssertTextAction,
    "assert_field_value": AssertFieldValueAction,
    "assert_url_contains": AssertUrlContainsAction,
    "extract_context": ExtractContextAction,
    "upload_file": UploadFileAction,
    "scroll": ScrollAction,
}


MAX_STEPS = 25
MAX_STEPS_TEST_FORM = 40  # a test matrix legitimately needs more actions
MAX_CONSECUTIVE_FAILURES = 3
MAX_ACTION_REPEAT = 4  # same action chosen this many times in a row → stuck
PAGE_TEXT_LIMIT_FOR_LLM = 5000
MAX_SCREENSHOT_BYTES = 4_000_000  # don't ship absurdly large frames to the LLM


# Structured Outputs schema for the Pilot decision. Strict mode requires every
# property to be present, so optional action params are nullable and stripped
# before Pydantic validation. This makes "Pilot returned an invalid action"
# structurally impossible (wrong SEMANTICS are still caught by the registry).
_NULLABLE_STR = {"type": ["string", "null"]}
_NULLABLE_INT = {"type": ["integer", "null"]}
_ACTION_PARAM_PROPS = {
    "action": {"type": "string", "enum": list(ACTION_REGISTRY.keys())},
    "url": _NULLABLE_STR,
    "text": _NULLABLE_STR,
    "role": _NULLABLE_STR,
    "name": _NULLABLE_STR,
    "label": _NULLABLE_STR,
    "placeholder": _NULLABLE_STR,
    "target": _NULLABLE_STR,
    "value": _NULLABLE_STR,
    "key": _NULLABLE_STR,
    "element_id": _NULLABLE_INT,
    "ms": _NULLABLE_INT,
    "timeout_ms": _NULLABLE_INT,
    "direction": {"type": ["string", "null"], "enum": ["down", "up", "bottom", "top", None]},
    "amount": _NULLABLE_INT,
    "paths": {"type": ["array", "null"], "items": {"type": "string"}},
    "description": _NULLABLE_STR,
}
PILOT_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["act", "done", "give_up"]},
        "thought": {"type": "string"},
        "summary": _NULLABLE_STR,
        "reason": _NULLABLE_STR,
        "action": {
            "type": ["object", "null"],
            "properties": _ACTION_PARAM_PROPS,
            "required": list(_ACTION_PARAM_PROPS.keys()),
            "additionalProperties": False,
        },
    },
    "required": ["decision", "thought", "summary", "reason", "action"],
    "additionalProperties": False,
}


def _action_sig(action_dict: dict) -> str:
    """Stable fingerprint of an action used to detect no-progress loops:
    the action kind plus its targeting params (ignoring free-text values like
    typed messages, which can legitimately vary)."""
    d = action_dict or {}
    keys = ("action", "url", "text", "role", "name", "label", "placeholder",
            "target", "key", "direction", "element_id")
    return "|".join(f"{k}={d.get(k)}" for k in keys if d.get(k) is not None)


def _page_fp(ctx: dict) -> str:
    """Coarse fingerprint of the page the Pilot is acting on. Includes the
    interactive-element labels so that a button flipping Connect→Pending (or a
    card leaving the list) counts as progress even if it happens past the
    visible_text truncation window."""
    ctx = ctx or {}
    text = (ctx.get("visible_text") or "")[:1500]
    inter = "\x1f".join(ctx.get("interactive_elements") or [])
    return f"{ctx.get('url', '')}\x1f{hash(text)}\x1f{hash(inter)}"


class AgentState(TypedDict, total=False):
    task_id: str
    session_id: str
    instruction: str
    mode: str  # "task" | "test_form"
    page_context: dict
    interpreted_task: str
    step_results: list[dict]
    act_trace: list[dict]
    consecutive_failures: int
    done: bool
    give_up: bool
    success: bool
    summary: str
    report_path: str
    error: str


# ---------- helpers ----------

async def _emit(task_id: str, event_type: str, **payload: Any) -> None:
    await bus.publish(task_id, {"type": event_type, "task_id": task_id, **payload})


async def _save_task(state: AgentState, status: TaskStatus) -> None:
    await db.upsert_task({
        "task_id": state["task_id"],
        "session_id": state["session_id"],
        "instruction": state["instruction"],
        "status": status.value,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "plan_json": json.dumps({
            "interpreted_task": state.get("interpreted_task", ""),
            "steps": [s.get("action") for s in state.get("step_results", [])],
        }) if state.get("step_results") else None,
        "steps_json": json.dumps(state.get("step_results", []), default=str),
        "report_path": state.get("report_path"),
        "success": int(state["success"]) if "success" in state else None,
        "summary": state.get("summary"),
    })


def _parse_action(raw: dict) -> Action:
    # The strict schema forces every param key to be present (as null when
    # unused); drop the nulls so Pydantic defaults apply.
    raw = {k: v for k, v in (raw or {}).items() if v is not None}
    kind = raw.get("action")
    cls = ACTION_REGISTRY.get(kind)
    if cls is None:
        raise ValueError(f"Disallowed action: {kind!r}")
    return cls(**raw)


def _screenshot_b64(ctx: dict) -> str | None:
    """Base64 of the latest observation screenshot for the multimodal Pilot."""
    p = (ctx or {}).get("screenshot_path")
    if not p:
        return None
    try:
        data = Path(p).read_bytes()
    except OSError:
        return None
    if not data or len(data) > MAX_SCREENSHOT_BYTES:
        return None
    return base64.b64encode(data).decode()


def _pilot_system(state: AgentState) -> str:
    system = prompts.PILOT_SYSTEM
    if state.get("mode") == "test_form":
        system += prompts.PILOT_TEST_FORM_ADDENDUM
    # Inject the domain playbook for wherever the agent currently IS, so it
    # adapts if the task navigates across domains mid-run.
    playbook = knowledge.load_playbook_for_url((state.get("page_context") or {}).get("url"))
    if playbook:
        system += (
            "\n\n--- DOMAIN PLAYBOOK ---\n"
            "Prior knowledge about THIS site (its sections, how to do things, "
            "and gotchas). Trust it over guessing, but if the live page "
            "contradicts it, believe the page (the site may have changed).\n\n"
            + playbook
        )
    return system


def _max_steps(state: AgentState) -> int:
    return MAX_STEPS_TEST_FORM if state.get("mode") == "test_form" else MAX_STEPS


def _trimmed_context(ctx: dict) -> dict:
    """Cap page text size for the LLM prompt."""
    out = dict(ctx)
    text = out.get("visible_text") or ""
    if len(text) > PAGE_TEXT_LIMIT_FOR_LLM:
        out["visible_text"] = text[:PAGE_TEXT_LIMIT_FOR_LLM] + "…[truncated]"
    out.pop("screenshot_path", None)
    return out


def _history_for_llm(steps: list[dict]) -> list[dict]:
    """Compact history for the LLM (last 8 steps, no screenshots/timestamps)."""
    out = []
    for s in steps[-8:]:
        out.append({
            "index": s.get("index"),
            "action": s.get("action"),
            "status": s.get("status"),
            "message": s.get("message"),
            "error": s.get("error"),
        })
    return out


# ---------- nodes ----------

async def node_observe(state: AgentState) -> AgentState:
    session = broker.get(state["session_id"])
    ctx: PageContext = await observe(session, state["task_id"])
    state["page_context"] = ctx.model_dump()
    if ctx.screenshot_path:
        await _emit(
            state["task_id"], "screenshot",
            path=relative_to_evidence(Path(ctx.screenshot_path)),
        )
    return state


async def node_pilot(state: AgentState) -> AgentState:
    state.setdefault("step_results", [])
    state.setdefault("consecutive_failures", 0)

    # Stop conditions
    max_steps = _max_steps(state)
    if len(state["step_results"]) >= max_steps:
        state["give_up"] = True
        state["success"] = False
        state["error"] = f"Reached max steps ({max_steps})"
        return state
    if state["consecutive_failures"] >= MAX_CONSECUTIVE_FAILURES:
        state["give_up"] = True
        state["success"] = False
        state["error"] = f"{MAX_CONSECUTIVE_FAILURES} consecutive failures"
        return state

    await _save_task(state, TaskStatus.EXECUTING)

    user_msg = json.dumps({
        "instruction": state["instruction"],
        "history": _history_for_llm(state["step_results"]),
        "page_context": _trimmed_context(state["page_context"]),
    }, ensure_ascii=False)

    raw = await llm.chat_json(
        _pilot_system(state),
        user_msg,
        image_b64=_screenshot_b64(state.get("page_context") or {}),
        schema=PILOT_DECISION_SCHEMA,
        schema_name="pilot_decision",
    )
    decision = (raw.get("decision") or "").lower()
    thought = raw.get("thought") or ""

    if decision == "done":
        state["done"] = True
        state["success"] = True
        state["summary"] = raw.get("summary") or thought
        await _emit(state["task_id"], "log", message=f"Pilot: DONE — {state['summary']}")
        return state

    if decision == "give_up":
        state["give_up"] = True
        state["success"] = False
        state["summary"] = raw.get("reason") or thought or "Agent gave up"
        await _emit(state["task_id"], "log", message=f"Pilot: GIVE UP — {state['summary']}")
        return state

    # decision == "act"
    try:
        action = _parse_action(raw.get("action") or {})
    except Exception as e:
        # Treat as a failure step so the Pilot sees it next round.
        idx = len(state["step_results"])
        state["step_results"].append({
            "index": idx,
            "action": raw.get("action") or {"action": "unknown"},
            "status": StepStatus.FAILED.value,
            "error": f"Invalid action from Pilot: {e}",
        })
        state["consecutive_failures"] += 1
        await _emit(state["task_id"], "log", message=f"Pilot returned invalid action: {e}", level="error")
        return state

    if not state.get("interpreted_task"):
        state["interpreted_task"] = thought
        await _emit(state["task_id"], "plan", plan={
            "interpreted_task": state["interpreted_task"],
            "steps": [],
        })

    # Loop guard: only a TRUE no-progress loop, i.e. the same action chosen
    # against an UNCHANGED page. Repeating "click Connect" on a list that keeps
    # changing (each click flips a button / drops a card) is legitimate
    # progress and must NOT trip this.
    sig = _action_sig(action.model_dump())
    fp = _page_fp(state.get("page_context") or {})
    trace = state.setdefault("act_trace", [])
    recent = trace[-(MAX_ACTION_REPEAT - 1):]
    if (
        sig
        and len(recent) == MAX_ACTION_REPEAT - 1
        and all(t.get("sig") == sig and t.get("fp") == fp for t in recent)
    ):
        state["give_up"] = True
        state["success"] = False
        state["error"] = f"Stuck in a loop: {sig}"
        state["summary"] = (
            f"Stuck in a loop: action ({sig}) repeated {MAX_ACTION_REPEAT} "
            f"times with no change to the page."
        )
        await _emit(state["task_id"], "log",
                    message=f"Loop detected — aborting: {sig}", level="error")
        return state
    trace.append({"sig": sig, "fp": fp})

    # Execute the chosen action immediately (single-step).
    session = broker.get(state["session_id"])
    idx = len(state["step_results"])
    await _emit(state["task_id"], "step_started", index=idx, action=action.model_dump())

    result = StepResult(
        index=idx,
        action=action.model_dump(),
        status=StepStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    try:
        outcome = await execute_action(session, state["task_id"], action)
        result.status = StepStatus.COMPLETED
        result.message = (outcome.get("message") or "") + (f" — {thought}" if thought else "")
        shot = outcome.get("screenshot")
        if shot:
            result.screenshot = relative_to_evidence(Path(shot))
        state["consecutive_failures"] = 0
    except ActionExecutionError as e:
        result.status = StepStatus.FAILED
        result.error = str(e)
        state["consecutive_failures"] += 1
    result.finished_at = datetime.now(timezone.utc)

    serialized = result.model_dump(mode="json")
    state["step_results"].append(serialized)
    await _emit(state["task_id"], "step_finished", step=serialized)
    return state


def route_after_pilot(state: AgentState) -> str:
    if state.get("done") or state.get("give_up"):
        return "reporter"
    return "observer"


def _failure_reason(state: AgentState) -> dict[str, Any]:
    """Machine-level explanation of why the run ended, separate from the LLM's
    prose summary. This is what tells us WHY a task that used to work now fails."""
    steps = state.get("step_results", [])
    failed = [s for s in steps if s.get("status") == "failed"]
    last_step = steps[-1] if steps else None
    if state.get("success"):
        category = "success"
    elif state.get("error"):
        # Set by node_pilot stop-conditions or the run_task crash handler.
        err = state["error"]
        if "max steps" in err.lower():
            category = "exhausted_max_steps"
        elif "consecutive failures" in err.lower():
            category = "too_many_failures"
        elif "loop" in err.lower():
            category = "stuck_loop"
        else:
            category = "internal_error"
    elif state.get("give_up"):
        category = "pilot_gave_up"
    else:
        category = "unknown"
    return {
        "category": category,
        "machine_reason": state.get("error"),
        "steps_attempted": len(steps),
        "steps_failed": len(failed),
        "last_step": last_step,
        "step_errors": [
            {"index": s.get("index"), "action": s.get("action"), "error": s.get("error")}
            for s in failed
        ],
    }


async def node_report(state: AgentState) -> AgentState:
    await _save_task(state, TaskStatus.REPORTING)
    await _emit(state["task_id"], "log", message="Reporter: writing evidence report…")

    ctx = state.get("page_context") or {}
    diagnostics = _failure_reason(state)
    final_state = {
        "final_url": ctx.get("url"),
        "final_title": ctx.get("title"),
        "what_the_agent_last_saw": (ctx.get("visible_text") or "")[:1500],
        "interactive_elements_seen": (ctx.get("interactive_elements") or [])[:40],
    }

    user_msg = json.dumps({
        "instruction": state["instruction"],
        "mode": state.get("mode", "task"),
        "interpreted_task": state.get("interpreted_task"),
        "success": state.get("success"),
        "summary": state.get("summary"),
        "diagnostics": diagnostics,
        "final_state": final_state,
        "steps": state.get("step_results", []),
    }, ensure_ascii=False, default=str)
    md = await llm.chat_text(prompts.REPORTER_SYSTEM, user_msg)

    folder = task_dir(state["session_id"], state["task_id"])
    report_path = folder / "report.md"
    write_text(report_path, md)
    write_json(folder / "steps.json", state.get("step_results", []))
    # Full machine-readable diagnostics for debugging regressions.
    write_json(folder / "debug.json", {
        "instruction": state["instruction"],
        "interpreted_task": state.get("interpreted_task"),
        "success": state.get("success"),
        "summary": state.get("summary"),
        "diagnostics": diagnostics,
        "final_page_context": ctx,
        "act_trace": state.get("act_trace", []),
        "step_results": state.get("step_results", []),
    })

    state["report_path"] = relative_to_evidence(report_path)

    await _save_task(state, TaskStatus.COMPLETED if state.get("success") else TaskStatus.FAILED)
    await _emit(
        state["task_id"], "completed",
        success=bool(state.get("success")),
        summary=state.get("summary"),
        report_path=state["report_path"],
    )
    return state


async def node_learn(state: AgentState) -> AgentState:
    """Distill durable, reusable knowledge about the domain and append it to its
    learned playbook. Best-effort — never affects the task outcome."""
    if not settings.knowledge_learning_enabled:
        return state
    ctx = state.get("page_context") or {}
    domain = knowledge.domain_for_url(ctx.get("url"))
    if not domain:
        return state
    try:
        user_msg = json.dumps({
            "instruction": state["instruction"],
            "success": state.get("success"),
            "summary": state.get("summary"),
            "steps": _history_for_llm(state.get("step_results", [])),
            "last_seen": (ctx.get("visible_text") or "")[:1500],
        }, ensure_ascii=False, default=str)
        findings = await llm.chat_text(prompts.LEARNER_SYSTEM, user_msg)
        knowledge.record_findings(domain, state["instruction"], findings)
        if findings and findings.strip().lower() != "none":
            await _emit(state["task_id"], "log",
                        message=f"Learner: updated knowledge for {domain}.")
    except Exception as e:  # noqa: BLE001 - learning must never break a run
        await _emit(state["task_id"], "log",
                    message=f"Learner skipped: {type(e).__name__}", level="error")
    return state


# ---------- driver ----------
#
# The agent is a plain ReAct loop: observer → pilot → (loop back to observer, or
# stop) → reporter. We drive it with a small async loop rather than a graph
# framework — the flow is trivial and a hand-rolled loop removes a heavy
# dependency chain (langgraph/langchain) that was both an install-fragility
# source (native wheels, Python-version pins) and a runtime-break source
# (version-skew errors like `module 'langchain' has no attribute 'debug'`).

async def _drive(state: AgentState) -> AgentState:
    # Hard ceiling on node visits so a logic bug can never spin forever. The
    # real budget is enforced inside node_pilot (MAX_STEPS); this is 2 nodes
    # (observe+pilot) per step plus generous slack.
    max_visits = _max_steps(state) * 3 + 10
    visits = 0
    while True:
        await node_observe(state)
        visits += 1
        await node_pilot(state)
        visits += 1
        if route_after_pilot(state) == "reporter":
            break
        if visits >= max_visits:
            # Safety net only — should be unreachable given node_pilot's caps.
            state["give_up"] = True
            state["success"] = False
            state["error"] = f"Reached max steps ({_max_steps(state)})"
            break
    await node_report(state)
    await node_learn(state)
    return state


async def run_task(
    task_id: str, session_id: str, instruction: str, mode: str = "task"
) -> AgentState:
    state: AgentState = {
        "task_id": task_id,
        "session_id": session_id,
        "instruction": instruction,
        "mode": mode,
        "step_results": [],
        "consecutive_failures": 0,
    }
    try:
        return await _drive(state)
    except Exception as e:
        # Any unhandled failure (Playwright wedged, LLM down, graph error) MUST
        # still close the loop for the UI — otherwise the WebSocket waits on a
        # `completed`/`error` frame that never comes and the task looks frozen.
        err = f"{type(e).__name__}: {e}"
        state["give_up"] = True
        state["success"] = False
        state["error"] = err
        state["summary"] = f"Task aborted due to an internal error: {err}"
        try:
            await db.upsert_task({
                "task_id": task_id,
                "session_id": session_id,
                "instruction": instruction,
                "status": TaskStatus.FAILED.value,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "plan_json": None,
                "steps_json": json.dumps(state.get("step_results", []), default=str),
                "report_path": state.get("report_path"),
                "success": 0,
                "summary": state["summary"],
            })
        except Exception:
            pass
        await _emit(
            task_id, "error",
            message=err,
            summary=state["summary"],
            success=False,
        )
        await _emit(
            task_id, "completed",
            success=False,
            summary=state["summary"],
            report_path=state.get("report_path"),
        )
        return state
