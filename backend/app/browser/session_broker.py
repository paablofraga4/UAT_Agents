"""Session Broker

Owns the lifecycle of Playwright browser sessions. Each session is associated with
a UUID `session_id`. The agent only ever sees this id — never cookies, tokens or
the underlying browser objects.

Implementation note: Playwright is driven through the SYNC API on a dedicated
worker thread. This isolates it from the host event loop (uvicorn forces
SelectorEventLoop on Windows, which can't spawn subprocesses; running Playwright
in our own thread sidesteps the issue and works on every OS).
"""
from __future__ import annotations

import asyncio
import threading
import uuid
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import datetime
from queue import Queue
from typing import Any, Callable, Optional

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from app.config import settings
from app.models.schemas import SessionStatus


# ---------------------------------------------------------------------------
# Worker thread: a single thread owns Playwright + all Page objects. Async code
# submits callables to it via _run() and awaits the result.
# ---------------------------------------------------------------------------

class _PlaywrightWorker:
    def __init__(self) -> None:
        self._queue: "Queue[tuple[Callable, Future]]" = Queue()
        self._thread = threading.Thread(target=self._loop, name="playwright-worker", daemon=True)
        self._started = threading.Event()
        self._pw = None
        self._browser: Optional[Browser] = None
        self._stop = False

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()
            self._started.wait()

    def _loop(self) -> None:
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=settings.headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._started.set()
        while not self._stop:
            fn, fut = self._queue.get()
            if fn is None:
                break
            try:
                fut.set_result(fn(self._browser))
            except Exception as e:
                fut.set_exception(e)
        try:
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass

    def submit(self, fn: Callable[[Browser], Any]) -> Future:
        fut: Future = Future()
        self._queue.put((fn, fut))
        return fut

    async def run(self, fn: Callable[[Browser], Any]) -> Any:
        loop = asyncio.get_running_loop()
        fut = self.submit(fn)
        return await loop.run_in_executor(None, fut.result)

    def shutdown(self) -> None:
        self._stop = True
        self._queue.put((None, Future()))  # type: ignore[arg-type]


_worker = _PlaywrightWorker()


# ---------------------------------------------------------------------------
# Session model + broker
# ---------------------------------------------------------------------------

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
        self._sessions: dict[str, BrowserSession] = {}
        self._init_lock = asyncio.Lock()

    async def create_session(self, url: str) -> BrowserSession:
        async with self._init_lock:
            _worker.start()

        session_id = uuid.uuid4().hex[:12]

        def _create(browser: Browser):
            ctx = browser.new_context(
                viewport={
                    "width": settings.browser_viewport_width,
                    "height": settings.browser_viewport_height,
                }
            )
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded")
            return ctx, page

        ctx, page = await _worker.run(_create)

        session = BrowserSession(
            session_id=session_id,
            url=url,
            status=SessionStatus.WAITING_LOGIN,
            created_at=datetime.utcnow(),
            context=ctx,
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
        storage_state_path = settings.sessions_dir / f"{session_id}.json"

        def _persist(_browser: Browser):
            session.context.storage_state(path=str(storage_state_path))

        await _worker.run(_persist)
        session.status = SessionStatus.AUTHENTICATED

    async def close_session(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return

        def _close(_browser: Browser):
            try:
                session.context.close()
            except Exception:
                pass

        try:
            await _worker.run(_close)
        finally:
            session.status = SessionStatus.CLOSED

    async def run_on_page(self, session_id: str, fn: Callable[[Page], Any]) -> Any:
        """Helper: run a sync Playwright callable against this session's page."""
        session = self.get(session_id)

        def _wrapped(_browser: Browser):
            return fn(session.page)

        return await _worker.run(_wrapped)

    async def shutdown(self) -> None:
        for sid in list(self._sessions.keys()):
            await self.close_session(sid)
        _worker.shutdown()


broker = SessionBroker()
