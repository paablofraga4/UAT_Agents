"""Test bootstrap.

Environment variables MUST be set before anything imports `app.config`
(the Settings singleton is created at import time), so this happens at
module import, not inside fixtures.
"""
import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="uat_agents_tests_"))
os.environ.setdefault("HEADLESS", "true")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("EVIDENCE_DIR", str(_TMP / "evidence"))
os.environ.setdefault("SESSIONS_DIR", str(_TMP / "sessions"))
os.environ.setdefault("DB_PATH", str(_TMP / "storage" / "uat_agents.db"))
os.environ.setdefault("UPLOADS_DIR", str(_TMP / "uploads"))

# Managed/CI environments ship a Chromium outside Playwright's cache. Use it
# when present so tests don't require `playwright install`.
if not os.environ.get("BROWSER_EXECUTABLE_PATH"):
    for cand in (
        "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
        "/opt/pw-browsers/chromium/chrome-linux/chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ):
        if Path(cand).is_file():
            os.environ["BROWSER_EXECUTABLE_PATH"] = cand
            break

import http.server  # noqa: E402
import threading  # noqa: E402

import pytest  # noqa: E402

from app.browser.session_broker import broker  # noqa: E402
from app.config import settings  # noqa: E402


FIXTURES_DIR = Path(__file__).parent / "fixtures"


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FIXTURES_DIR), **kwargs)

    def log_message(self, *args):  # silence per-request stderr noise
        pass


@pytest.fixture(scope="session")
def fixture_server():
    """Serve tests/fixtures over local HTTP; yields the base URL."""
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture
async def session_factory(fixture_server):
    """Create real browser sessions against the fixture app; closes them all
    at teardown."""
    created = []

    async def _make(page: str = "form.html"):
        session = await broker.create_session(f"{fixture_server}/{page}")
        created.append(session.session_id)
        return session

    yield _make
    for sid in created:
        await broker.close_session(sid)


@pytest.fixture
def uploads_dir() -> Path:
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    return settings.uploads_dir


@pytest.fixture(autouse=True)
def _isolate_knowledge_dirs(tmp_path, monkeypatch):
    """Keep every test's knowledge writes in a temp dir so a run (e.g. the
    graph tests that exercise node_learn) never pollutes the real committed
    playbooks or the runtime learned dir. Tests that need specific knowledge
    content override these paths themselves; this is just the safe default."""
    from app import knowledge

    monkeypatch.setattr(settings, "knowledge_dir", tmp_path / "kb")
    monkeypatch.setattr(settings, "knowledge_learned_dir", tmp_path / "kb_learned")
    settings.knowledge_dir.mkdir(parents=True, exist_ok=True)
    settings.knowledge_learned_dir.mkdir(parents=True, exist_ok=True)
    knowledge._cache.clear()
    yield
    knowledge._cache.clear()
