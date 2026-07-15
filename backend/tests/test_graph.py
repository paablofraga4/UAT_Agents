"""Agent-loop tests with the LLM and the browser mocked out: terminal states,
loop guard, failure accounting and the crash safety net."""
import asyncio
from types import SimpleNamespace

import pytest

from app.agents import graph as g
from app.agents.events import bus
from app.models.schemas import PageContext
from app.storage import db


@pytest.fixture(autouse=True)
async def _db():
    await db.init_db()


@pytest.fixture
def fake_browser(monkeypatch):
    """Stub broker/observe/execute_action; the pilot decisions come from a
    queue the test controls."""
    state = SimpleNamespace(
        decisions=[],           # queued pilot replies (dicts)
        executed=[],            # actions actually executed
        page_text="Página inicial",
        fail_execution=False,
        report_md="# report",
    )

    session = SimpleNamespace(lock=asyncio.Lock(), session_id="s1")
    monkeypatch.setattr(g, "broker", SimpleNamespace(get=lambda sid: session))

    async def fake_observe(sess, task_id):
        return PageContext(
            url="https://uat.example.com/",
            title="Fixture",
            visible_text=state.page_text,
            interactive_elements=["[0][button] Guardar"],
            screenshot_path=None,
        )

    async def fake_execute(sess, task_id, action):
        state.executed.append(action)
        if state.fail_execution:
            raise g.ActionExecutionError("boom")
        return {"message": "ok", "screenshot": None}

    async def fake_chat_json(system, user, **kwargs):
        assert state.decisions, "pilot asked for a decision but none queued"
        return state.decisions.pop(0)

    async def fake_chat_text(system, user, **kwargs):
        return state.report_md

    monkeypatch.setattr(g, "observe", fake_observe)
    monkeypatch.setattr(g, "execute_action", fake_execute)
    monkeypatch.setattr(g.llm, "chat_json", fake_chat_json)
    monkeypatch.setattr(g.llm, "chat_text", fake_chat_text)
    return state


def act(action: dict, thought="…"):
    return {"decision": "act", "action": action, "thought": thought,
            "summary": None, "reason": None}


async def test_happy_path_done(fake_browser):
    fake_browser.decisions = [
        act({"action": "click_text", "text": "Guardar"}),
        {"decision": "done", "summary": "Guardado", "thought": "…",
         "action": None, "reason": None},
    ]
    result = await g.run_task("t1", "s1", "guarda el registro")
    assert result["success"] is True
    assert len(fake_browser.executed) == 1
    assert result["report_path"]


async def test_give_up_is_reported_as_failure(fake_browser):
    fake_browser.decisions = [
        {"decision": "give_up", "reason": "CAPTCHA", "thought": "…",
         "action": None, "summary": None},
    ]
    result = await g.run_task("t2", "s1", "haz algo")
    assert result["success"] is False
    assert "CAPTCHA" in result["summary"]


async def test_structured_nulls_are_stripped_before_validation(fake_browser):
    # Strict-schema output: every param present, unused ones null.
    fake_browser.decisions = [
        act({"action": "click_text", "text": "Guardar", "url": None,
             "element_id": None, "paths": None, "description": None}),
        {"decision": "done", "summary": "ok", "thought": "…",
         "action": None, "reason": None},
    ]
    result = await g.run_task("t3", "s1", "guarda")
    assert result["success"] is True
    assert result["step_results"][0]["status"] == "completed"


async def test_invalid_action_becomes_failed_step_not_crash(fake_browser):
    fake_browser.decisions = [
        act({"action": "self_destruct"}),
        {"decision": "done", "summary": "ok", "thought": "…",
         "action": None, "reason": None},
    ]
    result = await g.run_task("t4", "s1", "haz algo")
    assert result["step_results"][0]["status"] == "failed"
    assert "Disallowed action" in result["step_results"][0]["error"]
    assert result["success"] is True  # pilot recovered


async def test_loop_guard_aborts_repeated_action_on_static_page(fake_browser):
    same = {"action": "click_text", "text": "Siguiente"}
    fake_browser.decisions = [act(same) for _ in range(g.MAX_ACTION_REPEAT + 1)]
    result = await g.run_task("t5", "s1", "pagina")
    assert result["success"] is False
    assert "loop" in result["summary"].lower()
    # The guard fires INSTEAD of executing the repeat.
    assert len(fake_browser.executed) == g.MAX_ACTION_REPEAT - 1


async def test_repeat_on_changing_page_is_not_a_loop(fake_browser):
    """Clicking 'Connect' repeatedly while the page changes each time is
    legitimate progress and must not trip the guard."""
    same = {"action": "click_text", "text": "Conectar"}
    n = g.MAX_ACTION_REPEAT + 2

    calls = {"count": 0}
    orig_observe = g.observe

    async def changing_observe(sess, task_id):
        ctx = await orig_observe(sess, task_id)
        calls["count"] += 1
        return ctx.model_copy(update={"visible_text": f"lista v{calls['count']}"})

    g.observe = changing_observe  # patched copy already installed by fixture
    try:
        fake_browser.decisions = [act(same) for _ in range(n)] + [
            {"decision": "done", "summary": "ok", "thought": "…",
             "action": None, "reason": None},
        ]
        result = await g.run_task("t6", "s1", "conecta con todos")
    finally:
        g.observe = orig_observe
    assert result["success"] is True
    assert len(fake_browser.executed) == n


async def test_three_consecutive_failures_stop_the_run(fake_browser):
    fake_browser.fail_execution = True
    fake_browser.decisions = [
        act({"action": "click_text", "text": f"btn{i}"}) for i in range(10)
    ]
    result = await g.run_task("t7", "s1", "haz algo")
    assert result["success"] is False
    assert "consecutive failures" in result["error"]
    assert len(fake_browser.executed) == g.MAX_CONSECUTIVE_FAILURES


async def test_crash_still_emits_completed_event(fake_browser, monkeypatch):
    async def exploding_observe(sess, task_id):
        raise RuntimeError("playwright wedged")

    monkeypatch.setattr(g, "observe", exploding_observe)

    events = bus.subscribe("t8")
    try:
        result = await g.run_task("t8", "s1", "haz algo")
        assert result["success"] is False

        seen = []
        while not events.empty():
            seen.append(events.get_nowait()["type"])
        assert "completed" in seen, f"UI would hang; events: {seen}"
    finally:
        bus.unsubscribe("t8", events)


async def test_test_form_mode_uses_extended_prompt_and_budget(fake_browser, monkeypatch):
    captured = {}

    async def spy_chat_json(system, user, **kwargs):
        captured["system"] = system
        return {"decision": "done", "summary": "matrix done", "thought": "…",
                "action": None, "reason": None}

    monkeypatch.setattr(g.llm, "chat_json", spy_chat_json)
    result = await g.run_task("t9", "s1", "testea el formulario", mode="test_form")
    assert result["success"] is True
    assert "FORM TEST MODE" in captured["system"]
    assert g._max_steps({"mode": "test_form"}) == g.MAX_STEPS_TEST_FORM
    assert g._max_steps({"mode": "task"}) == g.MAX_STEPS
