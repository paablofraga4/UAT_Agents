import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings


def task_dir(session_id: str, task_id: str) -> Path:
    p = settings.evidence_dir / session_id / task_id
    p.mkdir(parents=True, exist_ok=True)
    (p / "screenshots").mkdir(parents=True, exist_ok=True)
    return p


def screenshot_path(session_id: str, task_id: str, label: str) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    return task_dir(session_id, task_id) / "screenshots" / f"{ts}_{label}.png"


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def write_text(path: Path, payload: str) -> None:
    path.write_text(payload, encoding="utf-8")


def relative_to_evidence(path: Path) -> str:
    """Return a path string relative to the evidence root (for UI URLs)."""
    try:
        return str(path.relative_to(settings.evidence_dir)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")
