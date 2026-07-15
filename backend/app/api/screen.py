"""Live browser view + remote input.

`/ws/sessions/{id}/screen` streams JPEG frames of the session's page to the
frontend and accepts mouse/keyboard events back. This is what makes the
workspace fully remote: the user logs into the target app from the web UI —
no local Chromium window required (works with HEADLESS=true).

The credentials the user types travel browser→backend→Playwright only; they
are never observed, stored or given to the LLM. Frames are plain screenshots
polled at a modest rate (a few fps) — deliberately simple and robust with the
per-session sync worker model; CDP screencast can replace it later without
touching the protocol.

Client → server events (JSON):
  {"t":"click","x":123,"y":456,"button":"left","clicks":1}
  {"t":"move","x":123,"y":456}
  {"t":"wheel","x":123,"y":456,"dy":240}
  {"t":"key","key":"Enter","ctrl":false,"alt":false,"shift":false,"meta":false}
  {"t":"type","text":"pasted text"}
Server → client events:
  {"t":"meta","w":1366,"h":820}      (viewport size, sent first)
  {"t":"frame","data":"<base64 jpeg>"}
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from playwright.sync_api import Page

from app.api.deps import ws_token_ok
from app.browser.session_broker import SessionWedgedError, broker
from app.config import settings


logger = logging.getLogger(__name__)
router = APIRouter()

FRAME_INTERVAL_S = 0.4
FRAME_QUALITY = 55
INPUT_TIMEOUT_S = 10.0

# KeyboardEvent.key values map 1:1 to Playwright key names for the ones that
# matter; only a few need translation.
_KEY_ALIASES = {" ": "Space", "Spacebar": "Space"}


def _shoot(page: Page) -> bytes:
    return page.screenshot(type="jpeg", quality=FRAME_QUALITY, full_page=False)


def _apply_input(page: Page, ev: dict[str, Any]) -> None:
    t = ev.get("t")
    if t == "click":
        page.mouse.click(
            float(ev["x"]), float(ev["y"]),
            button=ev.get("button") or "left",
            click_count=int(ev.get("clicks") or 1),
        )
    elif t == "move":
        page.mouse.move(float(ev["x"]), float(ev["y"]))
    elif t == "wheel":
        page.mouse.move(float(ev.get("x") or 0), float(ev.get("y") or 0))
        page.mouse.wheel(0, float(ev.get("dy") or 0))
    elif t == "type":
        page.keyboard.insert_text(str(ev.get("text") or ""))
    elif t == "key":
        key = str(ev.get("key") or "")
        if not key:
            return
        key = _KEY_ALIASES.get(key, key)
        mods = [
            name for flag, name in (
                (ev.get("ctrl"), "Control"),
                (ev.get("alt"), "Alt"),
                (ev.get("meta"), "Meta"),
                (ev.get("shift"), "Shift"),
            ) if flag
        ]
        printable = len(key) == 1
        if printable and not any(m in mods for m in ("Control", "Alt", "Meta")):
            # insert_text is layout-independent and already shifted ("A", "@").
            page.keyboard.insert_text(key)
        else:
            combo = "+".join([m for m in mods if not (printable and m == "Shift")] + [key])
            page.keyboard.press(combo)


async def _send_frames(ws: WebSocket, session_id: str) -> None:
    last: bytes | None = None
    while True:
        try:
            data: bytes = await broker.run_on_page(session_id, _shoot, timeout=15.0)
        except SessionWedgedError:
            raise
        except Exception:
            # Page mid-navigation or busy — skip this frame.
            data = b""
        if data and data != last:
            last = data
            await ws.send_json({"t": "frame", "data": base64.b64encode(data).decode()})
        await asyncio.sleep(FRAME_INTERVAL_S)


async def _recv_input(ws: WebSocket, session_id: str) -> None:
    while True:
        ev = await ws.receive_json()
        try:
            await broker.run_on_page(
                session_id, lambda p: _apply_input(p, ev), timeout=INPUT_TIMEOUT_S
            )
        except SessionWedgedError:
            raise
        except Exception as e:
            logger.debug("screen input %s failed: %s", ev.get("t"), e)


@router.websocket("/ws/sessions/{session_id}/screen")
async def session_screen(ws: WebSocket, session_id: str) -> None:
    if not ws_token_ok(ws):
        await ws.close(code=1008)
        return
    try:
        broker.get(session_id)
    except KeyError:
        await ws.close(code=1008)
        return
    await ws.accept()
    await ws.send_json({
        "t": "meta",
        "w": settings.browser_viewport_width,
        "h": settings.browser_viewport_height,
    })

    sender = asyncio.create_task(_send_frames(ws, session_id))
    receiver = asyncio.create_task(_recv_input(ws, session_id))
    try:
        done, pending = await asyncio.wait(
            {sender, receiver}, return_when=asyncio.FIRST_EXCEPTION
        )
        for t in done:
            exc = t.exception()
            if exc is not None and not isinstance(
                exc, (WebSocketDisconnect, asyncio.CancelledError)
            ):
                logger.info("screen stream for %s ended: %s", session_id, exc)
    finally:
        for t in (sender, receiver):
            t.cancel()
        with contextlib.suppress(Exception):
            await ws.close()
