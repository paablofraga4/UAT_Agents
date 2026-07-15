import asyncio
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.agents.events import bus
from app.agents.graph import run_task
from app.api.deps import require_token
from app.browser.session_broker import broker
from app.models.schemas import (
    CreateTaskRequest,
    SessionStatus,
    TaskInfo,
    TaskStatus,
)
from app.storage import db


logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/tasks", tags=["tasks"], dependencies=[Depends(require_token)]
)

# Hold strong refs to in-flight tasks: a bare asyncio.create_task() can be
# garbage-collected mid-run, killing the task silently.
_running: set[asyncio.Task] = set()


def _spawn(task_id: str, session_id: str, instruction: str, mode: str) -> None:
    t = asyncio.create_task(run_task(task_id, session_id, instruction, mode))
    _running.add(t)

    def _done(fut: asyncio.Task) -> None:
        _running.discard(fut)
        # Free the session for the next task no matter how this one ended.
        _release_session(session_id, task_id)
        exc = fut.exception() if not fut.cancelled() else None
        if exc is not None:
            # run_task already handles its own errors, so reaching here means
            # something escaped even that. Last-resort: tell the client.
            logger.exception("run_task crashed", exc_info=exc)
            asyncio.create_task(bus.publish(task_id, {
                "type": "completed", "task_id": task_id,
                "success": False,
                "summary": f"Task crashed: {type(exc).__name__}: {exc}",
            }))

    t.add_done_callback(_done)


def _release_session(session_id: str, task_id: str) -> None:
    try:
        session = broker.get(session_id)
    except KeyError:
        return
    if session.active_task_id == task_id:
        session.active_task_id = None


@router.post("", response_model=TaskInfo)
async def create_task(req: CreateTaskRequest) -> TaskInfo:
    try:
        session = broker.get(req.session_id)
    except KeyError:
        raise HTTPException(404, "Session not found")
    if session.status not in (SessionStatus.AUTHENTICATED, SessionStatus.RUNNING):
        raise HTTPException(400, "Session is not authenticated. Confirm login first.")
    if session.active_task_id is not None:
        raise HTTPException(
            409,
            f"A task ({session.active_task_id}) is already running on this "
            "session. Wait for it to finish before submitting another.",
        )

    task_id = uuid.uuid4().hex[:12]
    session.active_task_id = task_id
    info = TaskInfo(
        task_id=task_id,
        session_id=req.session_id,
        instruction=req.instruction,
        status=TaskStatus.QUEUED,
        created_at=datetime.now(timezone.utc),
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

    # Fire-and-forget: the WebSocket stream will carry progress. The task ref
    # is retained so it can't be GC'd mid-run.
    _spawn(task_id, req.session_id, req.instruction, req.mode)

    return info


@router.get("/{task_id}")
async def get_task(task_id: str) -> dict:
    row = await db.get_task(task_id)
    if not row:
        raise HTTPException(404, "Task not found")
    return row
