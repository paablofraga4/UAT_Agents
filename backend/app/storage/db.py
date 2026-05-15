import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from app.config import settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    url        TEXT NOT NULL,
    status     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_screenshot TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id     TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    instruction TEXT NOT NULL,
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    plan_json   TEXT,
    steps_json  TEXT,
    report_path TEXT,
    success     INTEGER,
    summary     TEXT
);
"""


async def init_db() -> None:
    async with aiosqlite.connect(settings.db_path) as db:
        await db.executescript(SCHEMA)
        await db.commit()


async def upsert_session(row: dict[str, Any]) -> None:
    async with aiosqlite.connect(settings.db_path) as db:
        await db.execute(
            """INSERT INTO sessions(session_id, url, status, created_at, last_screenshot)
               VALUES(:session_id, :url, :status, :created_at, :last_screenshot)
               ON CONFLICT(session_id) DO UPDATE SET
                 status=excluded.status,
                 last_screenshot=excluded.last_screenshot""",
            row,
        )
        await db.commit()


async def get_session(session_id: str) -> Optional[dict[str, Any]]:
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM sessions WHERE session_id=?", (session_id,)
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def upsert_task(row: dict[str, Any]) -> None:
    async with aiosqlite.connect(settings.db_path) as db:
        await db.execute(
            """INSERT INTO tasks(task_id, session_id, instruction, status, created_at,
                                  plan_json, steps_json, report_path, success, summary)
               VALUES(:task_id, :session_id, :instruction, :status, :created_at,
                      :plan_json, :steps_json, :report_path, :success, :summary)
               ON CONFLICT(task_id) DO UPDATE SET
                 status=excluded.status,
                 plan_json=excluded.plan_json,
                 steps_json=excluded.steps_json,
                 report_path=excluded.report_path,
                 success=excluded.success,
                 summary=excluded.summary""",
            row,
        )
        await db.commit()


async def get_task(task_id: str) -> Optional[dict[str, Any]]:
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,))
        row = await cur.fetchone()
        return dict(row) if row else None
