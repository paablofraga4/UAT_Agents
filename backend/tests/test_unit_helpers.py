"""Pure-logic unit tests: action parsing, loop fingerprints, broker wedging."""
import time

import pytest

from app.agents.graph import PILOT_DECISION_SCHEMA, _action_sig, _page_fp, _parse_action
from app.browser.session_broker import SessionWedgedError, _SessionWorker
from app.models.schemas import ClickElementAction, ClickTextAction


def test_parse_action_strips_structured_nulls():
    a = _parse_action({
        "action": "click_text", "text": "Save", "url": None, "element_id": None,
        "paths": None, "description": None,
    })
    assert isinstance(a, ClickTextAction)
    assert a.text == "Save"
    assert a.description == ""


def test_parse_action_rejects_unknown_kind():
    with pytest.raises(ValueError, match="Disallowed action"):
        _parse_action({"action": "drop_database"})


def test_parse_click_element():
    a = _parse_action({"action": "click_element", "element_id": 7})
    assert isinstance(a, ClickElementAction)
    assert a.element_id == 7


def test_pilot_schema_is_strict_everywhere():
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object" or "object" in (node.get("type") or []):
                assert node.get("additionalProperties") is False
                assert set(node.get("required", [])) == set(node.get("properties", {}))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(PILOT_DECISION_SCHEMA)


def test_action_sig_distinguishes_element_ids():
    a = _action_sig({"action": "click_element", "element_id": 1})
    b = _action_sig({"action": "click_element", "element_id": 2})
    assert a != b


def test_action_sig_ignores_free_text_values():
    a = _action_sig({"action": "type_text", "target": "Message", "value": "hola"})
    b = _action_sig({"action": "type_text", "target": "Message", "value": "adiós"})
    assert a == b


def test_page_fp_changes_when_interactives_change():
    base = {"url": "u", "visible_text": "t", "interactive_elements": ["[0][button] Connect"]}
    after = dict(base, interactive_elements=["[0][button] Pending"])
    assert _page_fp(base) != _page_fp(after)


async def test_wedged_worker_is_quarantined():
    w = _SessionWorker("test-wedge")
    # Don't launch a browser — enqueue work directly on the (unstarted) queue
    # via a thread that behaves like the worker loop.
    import threading

    def loop():
        while True:
            fn, fut = w._queue.get()
            if fn is None:
                return
            try:
                fut.set_result(fn())
            except BaseException as e:  # noqa: BLE001
                fut.set_exception(e)

    threading.Thread(target=loop, daemon=True).start()

    assert await w.run(lambda: 42, timeout=5.0) == 42

    with pytest.raises(SessionWedgedError):
        await w.run(lambda: time.sleep(3), timeout=0.2)
    assert w.wedged

    # Once wedged, further calls are rejected immediately.
    with pytest.raises(SessionWedgedError):
        await w.run(lambda: 1, timeout=5.0)
    w.shutdown()
