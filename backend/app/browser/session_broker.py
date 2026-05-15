"""Session Broker

Owns the lifecycle of Playwright browser sessions. Each session is associated with
a UUID `session_id`. The agent only ever sees this id — never cookies, tokens or
the underlying browser objects.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from app.config import settings
from app.models.schemas import SessionStatus


@dataclass
class BrowserSession:
    session_id: str
    url: str
    status: SessionStatus
    created_at: datetime
    context: BrowserContext
    page: Page
    last_screenshot: Optional[str] = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class SessionBroker:
    def __init__(self) -> None:
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._sessions: dict[str, BrowserSession] = {}
        self._init_lock = asyncio.Lock()

    async def _ensure_browser(self) -> Browser:
        async with self._init_lock:
            if self._browser is None:
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=settings.headless,
                    args=["--disable-blink-features=AutomationControlled"],
                )
        return self._browser

    async def create_session(self, url: str) -> BrowserSession:
        browser = await self._ensure_browser()
        session_id = uuid.uuid4().hex[:12]
        storage_state_path = settings.sessions_dir / f"{session_id}.json"

        context = await browser.new_context(
            viewport={
                "width": settings.browser_viewport_width,
                "height": settings.browser_viewport_height,
            }
        )
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded")

        session = BrowserSession(
            session_id=session_id,
            url=url,
            status=SessionStatus.WAITING_LOGIN,
            created_at=datetime.utcnow(),
            context=context,
            page=page,
        )
        self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> BrowserSession:
        if session_id not in self._sessions:
            raise KeyError(f"Unknown session {session_id}")
        return self._sessions[session_id]

    async def confirm_login(self, session_id: str) -> None:
        session = self.get(session_id)
        # Persist the storage state locally so a future version can resume the session.
        storage_state_path = settings.sessions_dir / f"{session_id}.json"
        await session.context.storage_state(path=str(storage_state_path))
        session.status = SessionStatus.AUTHENTICATED

    async def close_session(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return
        try:
            await session.context.close()
        finally:
            session.status = SessionStatus.CLOSED

    async def shutdown(self) -> None:
        for sid in list(self._sessions.keys()):
            await self.close_session(sid)
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()


broker = SessionBroker()
