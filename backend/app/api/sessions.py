from datetime import datetime

from fastapi import APIRouter, HTTPException

from app.browser.session_broker import broker
from app.models.schemas import (
    CreateSessionRequest,
    SessionInfo,
    SessionStatus,
)
from app.storage import db


router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", response_model=SessionInfo)
async def create_session(req: CreateSessionRequest) -> SessionInfo:
    session = await broker.create_session(req.url)
    info = SessionInfo(
        session_id=session.session_id,
        url=session.url,
        status=session.status,
        created_at=session.created_at,
        last_screenshot=session.last_screenshot,
    )
    await db.upsert_session({
        "session_id": info.session_id,
        "url": info.url,
        "status": info.status.value,
        "created_at": info.created_at.isoformat(),
        "last_screenshot": info.last_screenshot,
    })
    return info


@router.post("/{session_id}/confirm-login", response_model=SessionInfo)
async def confirm_login(session_id: str) -> SessionInfo:
    try:
        await broker.confirm_login(session_id)
    except KeyError:
        raise HTTPException(404, "Session not found")
    s = broker.get(session_id)
    info = SessionInfo(
        session_id=s.session_id,
        url=s.url,
        status=s.status,
        created_at=s.created_at,
        last_screenshot=s.last_screenshot,
    )
    await db.upsert_session({
        "session_id": info.session_id,
        "url": info.url,
        "status": info.status.value,
        "created_at": info.created_at.isoformat(),
        "last_screenshot": info.last_screenshot,
    })
    return info


@router.delete("/{session_id}")
async def close_session(session_id: str) -> dict:
    await broker.close_session(session_id)
    return {"ok": True}


@router.get("/{session_id}", response_model=SessionInfo)
async def get_session(session_id: str) -> SessionInfo:
    try:
        s = broker.get(session_id)
    except KeyError:
        raise HTTPException(404, "Session not found")
    return SessionInfo(
        session_id=s.session_id,
        url=s.url,
        status=s.status,
        created_at=s.created_at,
        last_screenshot=s.last_screenshot,
    )
