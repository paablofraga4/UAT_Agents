"""API auth + evidence route hardening."""
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def secured(monkeypatch):
    monkeypatch.setattr(settings, "api_token", "s3cret")


def test_health_is_always_open(client, secured):
    assert client.get("/health").status_code == 200


def test_rest_requires_token_when_configured(client, secured):
    assert client.get("/sessions/nope").status_code == 401
    assert client.post("/tasks", json={"session_id": "x", "instruction": "y"}).status_code == 401


def test_rest_accepts_bearer_header_and_query_param(client, secured):
    r = client.get("/sessions/nope", headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 404  # authed, session simply doesn't exist
    assert client.get("/sessions/nope", headers={"X-API-Key": "s3cret"}).status_code == 404
    assert client.get("/sessions/nope?token=s3cret").status_code == 404
    assert client.get("/sessions/nope", headers={"X-API-Key": "wrong"}).status_code == 401


def test_open_mode_without_token(client):
    assert settings.api_token == ""
    assert client.get("/sessions/nope").status_code == 404


def test_evidence_serves_files_with_token(client, secured):
    settings.evidence_dir.mkdir(parents=True, exist_ok=True)
    (settings.evidence_dir / "r.md").write_text("report")
    assert client.get("/evidence/r.md").status_code == 401
    r = client.get("/evidence/r.md?token=s3cret")
    assert r.status_code == 200
    assert r.text == "report"


def test_evidence_blocks_path_traversal(client):
    outside = settings.evidence_dir.parent / "secret.txt"
    outside.write_text("nope")
    for path in ("../secret.txt", "%2e%2e/secret.txt", "..%2Fsecret.txt"):
        r = client.get(f"/evidence/{path}")
        assert r.status_code in (400, 404), f"traversal not blocked for {path}"


def test_task_on_busy_session_conflicts(client, monkeypatch):
    from types import SimpleNamespace

    from app.api import tasks as tasks_api
    from app.models.schemas import SessionStatus

    fake = SimpleNamespace(
        status=SessionStatus.AUTHENTICATED, active_task_id="running123"
    )
    monkeypatch.setattr(tasks_api.broker, "get", lambda sid: fake)
    r = client.post("/tasks", json={"session_id": "s", "instruction": "x"})
    assert r.status_code == 409
    assert "running123" in r.text
