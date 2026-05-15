import asyncio
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException

from app.agents.graph import run_task
from app.browser.session_broker import broker
from app.models.schemas import (
    CreateTaskRequest,
    SessionStatus,
    TaskInfo,
    TaskStatus,
)
from app.storage import db


router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("", response_model=TaskInfo)
async def create_task(req: CreateTaskRequest) -> TaskInfo:
    try:
        session = broker.get(req.session_id)
    except KeyError:
        raise HTTPException(404, "Session not found")
    if session.status not in (SessionStatus.AUTHENTICATED, SessionStatus.RUNNING):
        raise HTTPException(400, "Session is not authenticated. Confirm login first.")

    task_id = uuid.uuid4().hex[:12]
    info = TaskInfo(
        task_id=task_id,
        session_id=req.session_id,
        instruction=req.instruction,
        status=TaskStatus.QUEUED,
        created_at=datetime.utcnow(),
    )
    await db.upsert_task({
        "task_id": task_id,
        "session_id": req.session_id,
        "instruction": req.instruction,
        "status": TaskStatus.QUEUED.value,
        "created_at": info.created_at.isoformat(),
        "plan_json": None,
        "steps_json": None,
        "report_path": None,
        "success": None,
        "summary": None,
    })

    # Fire-and-forget: the WebSocket stream will carry progress.
    asyncio.create_task(run_task(task_id, req.session_id, req.instruction))

    return info


@router.get("/{task_id}")
async def get_task(task_id: str) -> dict:
    row = await db.get_task(task_id)
    if not row:
        raise HTTPException(404, "Task not found")
    return row
