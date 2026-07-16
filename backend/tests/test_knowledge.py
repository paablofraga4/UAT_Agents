"""Domain knowledge base: domain extraction, loading/concat, bounding, learned
append, and prompt injection into the Pilot."""
import pytest

from app import knowledge
from app.agents import graph as g
from app.config import settings


@pytest.fixture(autouse=True)
def isolate_knowledge(tmp_path, monkeypatch):
    """Point the knowledge dirs at a temp location and clear the read cache."""
    monkeypatch.setattr(settings, "knowledge_dir", tmp_path / "knowledge")
    monkeypatch.setattr(settings, "knowledge_learned_dir", tmp_path / "learned")
    settings.knowledge_dir.mkdir(parents=True, exist_ok=True)
    settings.knowledge_learned_dir.mkdir(parents=True, exist_ok=True)
    knowledge._cache.clear()
    yield


# ---------- domain extraction ----------

@pytest.mark.parametrize("url,expected", [
    ("https://www.linkedin.com/feed/", "linkedin.com"),
    ("https://linkedin.com", "linkedin.com"),
    ("http://app.example.co.uk/x?y=1", "app.example.co.uk"),
    ("https://WWW.LinkedIn.COM/", "linkedin.com"),
    ("not a url", None),
    ("", None),
    (None, None),
])
def test_domain_for_url(url, expected):
    assert knowledge.domain_for_url(url) == expected


# ---------- loading ----------

def test_load_empty_when_unknown():
    assert knowledge.load_playbook("unknown.com") == ""
    assert knowledge.load_playbook(None) == ""


def test_load_curated_only():
    knowledge.curated_path("linkedin.com").write_text("# LinkedIn\nReject cookies.")
    doc = knowledge.load_playbook("linkedin.com")
    assert "Reject cookies" in doc
    assert "Learned from previous runs" not in doc


def test_load_concatenates_curated_and_learned():
    knowledge.curated_path("x.com").write_text("CURATED BODY")
    knowledge.learned_path("x.com").write_text("LEARNED BODY")
    doc = knowledge.load_playbook("x.com")
    assert "CURATED BODY" in doc
    assert "LEARNED BODY" in doc
    # Curated comes first (most reliable).
    assert doc.index("CURATED BODY") < doc.index("LEARNED BODY")


def test_load_is_bounded():
    knowledge.curated_path("big.com").write_text("A" * (knowledge.MAX_PLAYBOOK_CHARS + 5000))
    doc = knowledge.load_playbook("big.com")
    assert len(doc) <= knowledge.MAX_PLAYBOOK_CHARS + 40
    assert "truncated" in doc


def test_cache_refreshes_on_mtime_change():
    p = knowledge.curated_path("c.com")
    p.write_text("v1")
    assert "v1" in knowledge.load_playbook("c.com")
    import os
    import time
    time.sleep(0.01)
    p.write_text("v2")
    os.utime(p, None)
    assert "v2" in knowledge.load_playbook("c.com")


# ---------- learning ----------

def test_record_findings_appends():
    knowledge.record_findings("d.com", "some task", "- nav label is 'Mi red'")
    learned = knowledge.learned_path("d.com").read_text()
    assert "Mi red" in learned
    assert "some task" in learned


def test_record_findings_ignores_empty_and_sentinels():
    knowledge.record_findings("d.com", "t", "")
    knowledge.record_findings("d.com", "t", "none")
    knowledge.record_findings("d.com", "t", "  N/A ")
    assert not knowledge.learned_path("d.com").exists()


def test_record_findings_keeps_newest_and_bounds_entries():
    for i in range(knowledge.MAX_LEARNED_ENTRIES + 10):
        knowledge.record_findings("d.com", f"task {i}", f"- finding {i}")
    learned = knowledge.learned_path("d.com").read_text()
    entries = [e for e in learned.split(knowledge._ENTRY_SEP) if e.strip()]
    assert len(entries) <= knowledge.MAX_LEARNED_ENTRIES
    assert "finding 0" not in learned          # oldest dropped
    assert f"finding {knowledge.MAX_LEARNED_ENTRIES + 9}" in learned  # newest kept


def test_record_findings_bounds_total_size():
    big = "- " + "x" * 2000
    for i in range(20):
        knowledge.record_findings("d.com", f"t{i}", big)
    assert len(knowledge.learned_path("d.com").read_text()) <= knowledge.MAX_LEARNED_CHARS + 200


def test_known_domains_lists_both_layers():
    knowledge.curated_path("a.com").write_text("x")
    knowledge.learned_path("b.com").write_text("y")
    assert knowledge.known_domains() == ["a.com", "b.com"]


# ---------- prompt injection ----------

def test_pilot_system_injects_domain_playbook():
    knowledge.curated_path("linkedin.com").write_text("REJECT THE COOKIE BANNER FIRST")
    state = {"mode": "task", "page_context": {"url": "https://www.linkedin.com/feed/"}}
    system = g._pilot_system(state)
    assert "DOMAIN PLAYBOOK" in system
    assert "REJECT THE COOKIE BANNER FIRST" in system


def test_pilot_system_no_playbook_when_domain_unknown():
    state = {"mode": "task", "page_context": {"url": "https://unknown-xyz.com/"}}
    system = g._pilot_system(state)
    assert "DOMAIN PLAYBOOK" not in system


def test_pilot_system_still_has_base_prompt():
    state = {"mode": "task", "page_context": {}}
    system = g._pilot_system(state)
    assert "Pilot agent" in system
