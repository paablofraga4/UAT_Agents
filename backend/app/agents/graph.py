"""LangGraph multi-agent workflow.

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

import json
from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from app.agents import llm, prompts
from app.agents.events import bus
from app.browser.runner import ActionExecutionError, execute_action, observe
from app.browser.session_broker import broker
from app.models.schemas import (
    Action,
    AssertTextAction,
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
    "fill_label": FillLabelAction,
    "fill_placeholder": FillPlaceholderAction,
    "type_text": TypeTextAction,
    "select_option": SelectOptionAction,
    "press_key": PressKeyAction,
    "wait": WaitAction,
    "wait_for_text": WaitForTextAction,
    "screenshot": ScreenshotAction,
    "assert_text": AssertTextAction,
    "extract_context": ExtractContextAction,
    "upload_file": UploadFileAction,
    "scroll": ScrollAction,
}


MAX_STEPS = 25
MAX_CONSECUTIVE_FAILURES = 3
MAX_ACTION_REPEAT = 4  # same action chosen this many times in a row → stuck
PAGE_TEXT_LIMIT_FOR_LLM = 5000


def _action_sig(action_dict: dict) -> str:
    """Stable fingerprint of an action used to detect no-progress loops:
    the action kind plus its targeting params (ignoring free-text values like
    typed messages, which can legitimately vary)."""
    d = action_dict or {}
    keys = ("action", "url", "text", "role", "name", "label", "placeholder",
            "target", "key", "direction")
    return "|".join(f"{k}={d.get(k)}" for k in keys if d.get(k) is not None)


class AgentState(TypedDict, total=False):
    task_id: str
    session_id: str
    instruction: str
    page_context: dict
    interpreted_task: str
    step_results: list[dict]
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
        "created_at": datetime.utcnow().isoformat(),
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
    kind = raw.get("action")
    cls = ACTION_REGISTRY.get(kind)
    if cls is None:
        raise ValueError(f"Disallowed action: {kind!r}")
    return cls(**raw)


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
    if len(state["step_results"]) >= MAX_STEPS:
        state["give_up"] = True
        state["error"] = f"Reached max steps ({MAX_STEPS})"
        return state
    if state["consecutive_failures"] >= MAX_CONSECUTIVE_FAILURES:
        state["give_up"] = True
        state["error"] = f"{MAX_CONSECUTIVE_FAILURES} consecutive failures"
        return state

    await _save_task(state, TaskStatus.EXECUTING)

    user_msg = json.dumps({
        "instruction": state["instruction"],
        "history": _history_for_llm(state["step_results"]),
        "page_context": _trimmed_context(state["page_context"]),
    }, ensure_ascii=False)

    raw = await llm.chat_json(prompts.PILOT_SYSTEM, user_msg)
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

    # Loop guard: if the Pilot keeps choosing the SAME action (same target),
    # it's stuck — re-observing doesn't help, abort instead of burning the
    # remaining steps and API calls.
    sig = _action_sig(action.model_dump())
    recent = [_action_sig(s.get("action") or {}) for s in state["step_results"][-(MAX_ACTION_REPEAT - 1):]]
    if len(recent) == MAX_ACTION_REPEAT - 1 and all(r == sig for r in recent) and sig:
        state["give_up"] = True
        state["success"] = False
        state["summary"] = (
            f"Stuck in a loop: the same action ({sig}) was repeated "
            f"{MAX_ACTION_REPEAT} times without progress."
        )
        await _emit(state["task_id"], "log",
                    message=f"Loop detected — aborting: {sig}", level="error")
        return state

    # Execute the chosen action immediately (single-step).
    session = broker.get(state["session_id"])
    idx = len(state["step_results"])
    await _emit(state["task_id"], "step_started", index=idx, action=action.model_dump())

    result = StepResult(
        index=idx,
        action=action.model_dump(),
        status=StepStatus.RUNNING,
        started_at=datetime.utcnow(),
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
    result.finished_at = datetime.utcnow()

    serialized = result.model_dump(mode="json")
    state["step_results"].append(serialized)
    await _emit(state["task_id"], "step_finished", step=serialized)
    return state


def route_after_pilot(state: AgentState) -> str:
    if state.get("done") or state.get("give_up"):
        return "reporter"
    return "observer"


async def node_report(state: AgentState) -> AgentState:
    await _save_task(state, TaskStatus.REPORTING)
    await _emit(state["task_id"], "log", message="Reporter: writing evidence report…")

    user_msg = json.dumps({
        "instruction": state["instruction"],
        "interpreted_task": state.get("interpreted_task"),
        "success": state.get("success"),
        "summary": state.get("summary"),
        "steps": state.get("step_results", []),
    }, ensure_ascii=False, default=str)
    md = await llm.chat_text(prompts.REPORTER_SYSTEM, user_msg)

    folder = task_dir(state["session_id"], state["task_id"])
    report_path = folder / "report.md"
    write_text(report_path, md)
    write_json(folder / "steps.json", state.get("step_results", []))

    state["report_path"] = relative_to_evidence(report_path)

    await _save_task(state, TaskStatus.COMPLETED if state.get("success") else TaskStatus.FAILED)
    await _emit(
        state["task_id"], "completed",
        success=bool(state.get("success")),
        summary=state.get("summary"),
        report_path=state["report_path"],
    )
    return state


# ---------- graph ----------

def build_graph():
    g = StateGraph(AgentState)
    g.add_node("observer", node_observe)
    g.add_node("pilot", node_pilot)
    g.add_node("reporter", node_report)

    g.set_entry_point("observer")
    g.add_edge("observer", "pilot")
    g.add_conditional_edges("pilot", route_after_pilot, {
        "observer": "observer",
        "reporter": "reporter",
    })
    g.add_edge("reporter", END)
    return g.compile()


graph = build_graph()


async def run_task(task_id: str, session_id: str, instruction: str) -> AgentState:
    state: AgentState = {
        "task_id": task_id,
        "session_id": session_id,
        "instruction": instruction,
        "step_results": [],
        "consecutive_failures": 0,
    }
    try:
        return await graph.ainvoke(
            state, config={"recursion_limit": MAX_STEPS * 3 + 10}
        )
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
                "created_at": datetime.utcnow().isoformat(),
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
