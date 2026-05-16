"""Session Broker

Owns the lifecycle of Playwright browser sessions. Each session is associated with
a UUID `session_id`. The agent only ever sees this id — never cookies, tokens or
the underlying browser objects.

Isolation model: EACH session owns its own worker thread, its own Playwright
instance and its own Chromium browser. A call that wedges (a stuck navigation, a
native OS dialog opened by an errant click, a hung evaluate) can therefore only
freeze ITS OWN session — never the whole process. Every call is also bounded by
a timeout on the async side via `asyncio.wrap_future`, so the caller never hangs
forever even if the worker thread is stuck; the affected session is marked ERROR
so new tasks are rejected instead of silently queuing behind a dead thread.

Implementation note: Playwright is driven through the SYNC API on these worker
threads. This isolates it from the host event loop (uvicorn forces a specific
loop policy on Windows; running Playwright in our own thread sidesteps subprocess
issues and works on every OS).
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


# Default ceiling for any single Playwright call. Individual Playwright actions
# carry their own (shorter) timeouts; this is the backstop so a truly wedged
# call can't hang the async caller forever.
DEFAULT_CALL_TIMEOUT_S = 45.0


class SessionWedgedError(RuntimeError):
    """Raised when a session's worker thread did not return within the timeout.
    The session is considered unusable from this point on."""


# ---------------------------------------------------------------------------
# Per-session worker thread: owns Playwright + the browser + context + page.
# ---------------------------------------------------------------------------

class _SessionWorker:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._queue: "Queue[tuple[Optional[Callable], Future]]" = Queue()
        self._thread = threading.Thread(
            target=self._loop, name=f"pw-{session_id}", daemon=True
        )
        self._started = threading.Event()
        self._start_error: Optional[BaseException] = None
        self._pw = None
        self._browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self._stop = False
        self.wedged = False

    # --- thread lifecycle ---------------------------------------------------

    def start(self) -> None:
        self._thread.start()
        self._started.wait()
        if self._start_error is not None:
            raise self._start_error

    def _loop(self) -> None:
        try:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(
                headless=settings.headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
        except BaseException as e:  # noqa: BLE001 - surface launch failures to caller
            self._start_error = e
            self._started.set()
            return
        self._started.set()
        while not self._stop:
            fn, fut = self._queue.get()
            if fn is None:
                break
            if fut.cancelled():
                continue
            try:
                value = fn()
            except BaseException as e:  # noqa: BLE001 - propagate to the awaiting caller
                if not fut.cancelled():
                    fut.set_exception(e)
                continue
            if not fut.cancelled():
                fut.set_result(value)
        try:
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass

    # --- call submission ----------------------------------------------------

    async def run(self, fn: Callable[[], Any], *, timeout: float = DEFAULT_CALL_TIMEOUT_S) -> Any:
        if self.wedged:
            raise SessionWedgedError(f"session {self.session_id} is wedged")
        fut: Future = Future()
        self._queue.put((fn, fut))
        try:
            # wrap_future bridges the worker-thread Future onto the event loop
            # WITHOUT parking an executor thread on .result().
            return await asyncio.wait_for(asyncio.wrap_future(fut), timeout=timeout)
        except (asyncio.TimeoutError, TimeoutError) as e:
            # The worker thread is still running the wedged callable. Anything
            # queued behind it would never run, so quarantine the session.
            self.wedged = True
            fut.cancel()
            raise SessionWedgedError(
                f"Playwright call timed out after {timeout}s; session "
                f"{self.session_id} quarantined"
            ) from e

    def shutdown(self) -> None:
        self._stop = True
        self._queue.put((None, Future()))


# ---------------------------------------------------------------------------
# Session model + broker
# ---------------------------------------------------------------------------

@dataclass
class BrowserSession:
    session_id: str
    url: str
    status: SessionStatus
    created_at: datetime
    worker: _SessionWorker
    last_screenshot: Optional[str] = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def context(self) -> BrowserContext:
        assert self.worker.context is not None
        return self.worker.context

    @property
    def page(self) -> Page:
        assert self.worker.page is not None
        return self.worker.page


class SessionBroker:
    def __init__(self) -> None:
        self._sessions: dict[str, BrowserSession] = {}
        self._init_lock = asyncio.Lock()

    async def create_session(self, url: str) -> BrowserSession:
        session_id = uuid.uuid4().hex[:12]
        worker = _SessionWorker(session_id)

        # Starting the thread blocks until Playwright + the browser are ready
        # (or it raises the launch error). Do it off the event loop.
        await asyncio.get_running_loop().run_in_executor(None, worker.start)

        def _create() -> None:
            assert worker._browser is not None
            ctx = worker._browser.new_context(
                viewport={
                    "width": settings.browser_viewport_width,
                    "height": settings.browser_viewport_height,
                }
            )
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded")
            worker.context = ctx
            worker.page = page

        await worker.run(_create, timeout=60.0)

        session = BrowserSession(
            session_id=session_id,
            url=url,
            status=SessionStatus.WAITING_LOGIN,
            created_at=datetime.utcnow(),
            worker=worker,
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

        def _persist() -> None:
            session.context.storage_state(path=str(storage_state_path))

        await session.worker.run(_persist)
        session.status = SessionStatus.AUTHENTICATED

    async def close_session(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return

        if not session.worker.wedged:
            def _close() -> None:
                try:
                    session.context.close()
                except Exception:
                    pass
            try:
                await session.worker.run(_close, timeout=10.0)
            except Exception:
                pass

        session.worker.shutdown()
        session.status = SessionStatus.CLOSED

    async def run_on_page(self, session_id: str, fn: Callable[[Page], Any], *, timeout: float = DEFAULT_CALL_TIMEOUT_S) -> Any:
        """Run a sync Playwright callable against this session's page."""
        session = self.get(session_id)
        worker = session.worker

        def _wrapped() -> Any:
            return fn(worker.page)

        try:
            return await worker.run(_wrapped, timeout=timeout)
        except SessionWedgedError:
            session.status = SessionStatus.ERROR
            raise

    async def shutdown(self) -> None:
        for sid in list(self._sessions.keys()):
            await self.close_session(sid)


broker = SessionBroker()
