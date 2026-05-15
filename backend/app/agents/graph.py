"""LangGraph-based multi-agent workflow.

Nodes:
    observe   → percibe el estado actual de la página
    plan      → genera un Plan estructurado (allowlist)
    execute   → ejecuta cada paso a través del Browser Runner
    validate  → comprueba si el objetivo se cumplió
    report    → escribe el informe Markdown de evidencias

Flujo: observe → plan → execute → validate → report
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
    Plan,
    PressKeyAction,
    ScreenshotAction,
    SelectOptionAction,
    StepResult,
    StepStatus,
    TaskStatus,
    TypeTextAction,
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
}


class AgentState(TypedDict, total=False):
    task_id: str
    session_id: str
    instruction: str
    page_context: dict
    plan: dict
    step_results: list[dict]
    success: bool
    summary: str
    report_path: str
    failed: bool
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
        "plan_json": json.dumps(state.get("plan")) if state.get("plan") else None,
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


# ---------- nodes ----------

async def node_observe(state: AgentState) -> AgentState:
    session = broker.get(state["session_id"])
    await _emit(state["task_id"], "log", message="Observing current page…")
    ctx: PageContext = await observe(session, state["task_id"])
    state["page_context"] = ctx.model_dump()
    await _emit(
        state["task_id"], "screenshot",
        path=relative_to_evidence(Path(ctx.screenshot_path)) if ctx.screenshot_path else None,
    )
    return state


async def node_plan(state: AgentState) -> AgentState:
    await _save_task(state, TaskStatus.PLANNING)
    await _emit(state["task_id"], "log", message="Planner: generating plan…")

    user_msg = json.dumps({
        "instruction": state["instruction"],
        "page_context": state["page_context"],
    }, ensure_ascii=False)

    raw = await llm.chat_json(prompts.PLANNER_SYSTEM, user_msg)

    # Validate strictly via Pydantic — disallowed actions raise here.
    try:
        steps = [_parse_action(s) for s in raw.get("steps", [])]
        plan = Plan(interpreted_task=raw.get("interpreted_task", ""), steps=steps)
    except Exception as e:
        state["failed"] = True
        state["error"] = f"Invalid plan from Planner: {e}"
        await _emit(state["task_id"], "error", message=state["error"])
        return state

    state["plan"] = plan.model_dump()
    await _emit(state["task_id"], "plan", plan=state["plan"])
    return state


async def node_execute(state: AgentState) -> AgentState:
    if state.get("failed"):
        return state
    await _save_task(state, TaskStatus.EXECUTING)

    session = broker.get(state["session_id"])
    plan = Plan(**state["plan"])
    results: list[dict] = []

    for idx, action in enumerate(plan.steps):
        result = StepResult(
            index=idx,
            action=action.model_dump(),
            status=StepStatus.RUNNING,
            started_at=datetime.utcnow(),
        )
        await _emit(state["task_id"], "step_started", index=idx, action=action.model_dump())
        try:
            outcome = await execute_action(session, state["task_id"], action)
            result.status = StepStatus.COMPLETED
            result.message = outcome.get("message")
            shot = outcome.get("screenshot")
            if shot:
                result.screenshot = relative_to_evidence(Path(shot))
        except ActionExecutionError as e:
            result.status = StepStatus.FAILED
            result.error = str(e)
        result.finished_at = datetime.utcnow()
        results.append(result.model_dump(mode="json"))
        await _emit(state["task_id"], "step_finished", step=results[-1])

        if result.status == StepStatus.FAILED:
            state["failed"] = True
            state["error"] = result.error or "Step failed"
            break

    state["step_results"] = results
    return state


async def node_validate(state: AgentState) -> AgentState:
    await _save_task(state, TaskStatus.VALIDATING)
    await _emit(state["task_id"], "log", message="Validator: checking outcome…")

    # Re-observe to get the final page context
    session = broker.get(state["session_id"])
    ctx = await observe(session, state["task_id"])
    state["page_context"] = ctx.model_dump()

    if state.get("failed"):
        state["success"] = False
        state["summary"] = state.get("error", "A step failed; execution stopped.")
        return state

    user_msg = json.dumps({
        "instruction": state["instruction"],
        "steps": state["step_results"],
        "final_page_context": state["page_context"],
    }, ensure_ascii=False, default=str)
    verdict = await llm.chat_json(prompts.VALIDATOR_SYSTEM, user_msg)
    state["success"] = bool(verdict.get("success", False))
    state["summary"] = str(verdict.get("summary", ""))
    return state


async def node_report(state: AgentState) -> AgentState:
    await _save_task(state, TaskStatus.REPORTING)
    await _emit(state["task_id"], "log", message="Reporter: writing evidence report…")

    user_msg = json.dumps({
        "instruction": state["instruction"],
        "interpreted_task": state.get("plan", {}).get("interpreted_task"),
        "success": state.get("success"),
        "summary": state.get("summary"),
        "steps": state.get("step_results", []),
    }, ensure_ascii=False, default=str)
    md = await llm.chat_text(prompts.REPORTER_SYSTEM, user_msg)

    folder = task_dir(state["session_id"], state["task_id"])
    report_path = folder / "report.md"
    write_text(report_path, md)
    write_json(folder / "plan.json", state.get("plan"))
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
    g.add_node("planner", node_plan)
    g.add_node("executor", node_execute)
    g.add_node("validator", node_validate)
    g.add_node("reporter", node_report)

    g.set_entry_point("observer")
    g.add_edge("observer", "planner")
    g.add_edge("planner", "executor")
    g.add_edge("executor", "validator")
    g.add_edge("validator", "reporter")
    g.add_edge("reporter", END)
    return g.compile()


graph = build_graph()


async def run_task(task_id: str, session_id: str, instruction: str) -> AgentState:
    state: AgentState = {
        "task_id": task_id,
        "session_id": session_id,
        "instruction": instruction,
    }
    return await graph.ainvoke(state)
