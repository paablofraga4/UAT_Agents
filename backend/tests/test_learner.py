"""node_learn: persists distilled findings after a task, best-effort."""
import pytest

from app import knowledge
from app.agents import graph as g
from app.config import settings


@pytest.fixture(autouse=True)
def isolate_knowledge(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "knowledge_dir", tmp_path / "knowledge")
    monkeypatch.setattr(settings, "knowledge_learned_dir", tmp_path / "learned")
    monkeypatch.setattr(settings, "knowledge_learning_enabled", True)
    settings.knowledge_dir.mkdir(parents=True, exist_ok=True)
    settings.knowledge_learned_dir.mkdir(parents=True, exist_ok=True)
    knowledge._cache.clear()
    yield


def _state(url="https://www.linkedin.com/messaging/"):
    return {
        "task_id": "t", "session_id": "s", "instruction": "send a message",
        "page_context": {"url": url, "visible_text": "Mensajes"},
        "step_results": [],
    }


async def test_learn_writes_findings(monkeypatch):
    async def fake_chat_text(system, user, **kw):
        return "- To message: open Mensajes, use 'Buscar mensajes'"

    monkeypatch.setattr(g.llm, "chat_text", fake_chat_text)
    await g.node_learn(_state())
    learned = knowledge.learned_path("linkedin.com").read_text()
    assert "Buscar mensajes" in learned


async def test_learn_respects_none_sentinel(monkeypatch):
    async def fake_chat_text(system, user, **kw):
        return "none"

    monkeypatch.setattr(g.llm, "chat_text", fake_chat_text)
    await g.node_learn(_state())
    assert not knowledge.learned_path("linkedin.com").exists()


async def test_learn_disabled_by_setting(monkeypatch):
    monkeypatch.setattr(settings, "knowledge_learning_enabled", False)
    called = False

    async def fake_chat_text(system, user, **kw):
        nonlocal called
        called = True
        return "- x"

    monkeypatch.setattr(g.llm, "chat_text", fake_chat_text)
    await g.node_learn(_state())
    assert called is False


async def test_learn_never_raises_on_llm_error(monkeypatch):
    async def boom(system, user, **kw):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(g.llm, "chat_text", boom)
    # Must not raise — learning is best-effort.
    await g.node_learn(_state())


async def test_learn_noop_when_no_domain(monkeypatch):
    called = False

    async def fake_chat_text(system, user, **kw):
        nonlocal called
        called = True
        return "- x"

    monkeypatch.setattr(g.llm, "chat_text", fake_chat_text)
    await g.node_learn(_state(url="about:blank"))
    assert called is False
