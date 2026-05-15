"""Browser Runner

Mediates ALL agent → browser interaction. The agent never touches Playwright
directly. Every action is matched against the allowlisted Pydantic types and
executed through user-facing locators (`getByText`, `getByRole`, `getByLabel`,
`getByPlaceholder`) for resilient automation.

All Playwright calls go through the SessionBroker's worker thread (sync API),
which keeps Playwright off the host event loop entirely.
"""
from __future__ import annotations

import time
from typing import Any

from playwright.sync_api import Page, TimeoutError as PWTimeoutError

from app.browser.session_broker import BrowserSession, broker
from app.models.schemas import (
    Action,
    AssertTextAction,
    ClickRoleAction,
    ClickTextAction,
    ExtractContextAction,
    FillLabelAction,
    FillPlaceholderAction,
    GotoAction,
    PageContext,
    PressKeyAction,
    ScreenshotAction,
    SelectOptionAction,
    WaitAction,
    WaitForTextAction,
)
from app.storage.evidence import screenshot_path


DEFAULT_TIMEOUT_MS = 8000
MAX_VISIBLE_TEXT = 6000
MAX_INTERACTIVE = 60


class ActionExecutionError(Exception):
    pass


_INTERACTIVE_JS = r"""
() => {
    const out = [];
    const seen = new Set();
    const push = (kind, label) => {
        if (!label) return;
        const t = label.trim().slice(0, 80);
        if (!t) return;
        const key = kind + '::' + t;
        if (seen.has(key)) return;
        seen.add(key);
        out.push(`[${kind}] ${t}`);
    };
    document.querySelectorAll('button, a, [role=button], [role=link], [role=tab]').forEach(el => {
        push(el.tagName.toLowerCase(), el.innerText || el.getAttribute('aria-label'));
    });
    document.querySelectorAll('input, textarea, select').forEach(el => {
        const id = el.getAttribute('id');
        let label = '';
        if (id) {
            const l = document.querySelector(`label[for="${CSS.escape(id)}"]`);
            if (l) label = l.innerText;
        }
        label = label || el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('name') || el.tagName;
        push(el.tagName.toLowerCase(), label);
    });
    return out;
}
"""


def _take_screenshot_sync(page: Page, path_str: str) -> None:
    page.screenshot(path=path_str, full_page=False)


async def take_screenshot(session: BrowserSession, task_id: str, label: str) -> str:
    path = screenshot_path(session.session_id, task_id, label)
    path_str = str(path)
    try:
        await broker.run_on_page(session.session_id, lambda p: _take_screenshot_sync(p, path_str))
    except Exception as e:
        raise ActionExecutionError(f"screenshot failed: {e}") from e
    session.last_screenshot = path_str
    return path_str


def _observe_sync(page: Page) -> dict[str, Any]:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
    except PWTimeoutError:
        pass
    url = page.url
    title = page.title()
    try:
        body = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
    except Exception:
        body = ""
    body = body[:MAX_VISIBLE_TEXT]
    try:
        interactive = page.evaluate(_INTERACTIVE_JS) or []
    except Exception:
        interactive = []
    return {"url": url, "title": title, "visible_text": body, "interactive_elements": interactive[:MAX_INTERACTIVE]}


async def observe(session: BrowserSession, task_id: str) -> PageContext:
    snap = await broker.run_on_page(session.session_id, _observe_sync)
    shot = await take_screenshot(session, task_id, "observe")
    return PageContext(
        url=snap["url"],
        title=snap["title"],
        visible_text=snap["visible_text"],
        interactive_elements=snap["interactive_elements"],
        screenshot_path=shot,
    )


def _execute_sync(page: Page, action: Action) -> str:
    if isinstance(action, GotoAction):
        page.goto(action.url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT_MS * 2)
        return f"Navigated to {action.url}"
    if isinstance(action, ClickTextAction):
        page.get_by_text(action.text, exact=False).first.click(timeout=DEFAULT_TIMEOUT_MS)
        return f"Clicked text {action.text!r}"
    if isinstance(action, ClickRoleAction):
        page.get_by_role(action.role, name=action.name).first.click(timeout=DEFAULT_TIMEOUT_MS)
        return f"Clicked {action.role}={action.name!r}"
    if isinstance(action, FillLabelAction):
        page.get_by_label(action.label, exact=False).first.fill(action.value, timeout=DEFAULT_TIMEOUT_MS)
        return f"Filled label {action.label!r}"
    if isinstance(action, FillPlaceholderAction):
        page.get_by_placeholder(action.placeholder, exact=False).first.fill(action.value, timeout=DEFAULT_TIMEOUT_MS)
        return f"Filled placeholder {action.placeholder!r}"
    if isinstance(action, SelectOptionAction):
        page.get_by_label(action.label, exact=False).first.select_option(label=action.value, timeout=DEFAULT_TIMEOUT_MS)
        return f"Selected {action.value!r} in {action.label!r}"
    if isinstance(action, PressKeyAction):
        page.keyboard.press(action.key)
        return f"Pressed {action.key}"
    if isinstance(action, WaitAction):
        time.sleep(action.ms / 1000)
        return f"Waited {action.ms}ms"
    if isinstance(action, WaitForTextAction):
        page.get_by_text(action.text, exact=False).first.wait_for(state="visible", timeout=action.timeout_ms)
        return f"Text {action.text!r} appeared"
    if isinstance(action, ScreenshotAction):
        return "Screenshot captured"
    if isinstance(action, AssertTextAction):
        page.get_by_text(action.text, exact=False).first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT_MS)
        return f"Asserted text {action.text!r} visible"
    raise ActionExecutionError(f"Unsupported action: {type(action).__name__}")


async def execute_action(session: BrowserSession, task_id: str, action: Action) -> dict[str, Any]:
    """Run a single allowlisted action. Returns dict with screenshot + msg."""
    if isinstance(action, ExtractContextAction):
        ctx = await observe(session, task_id)
        return {
            "message": "Extracted page context",
            "screenshot": ctx.screenshot_path,
            "context": ctx.model_dump(),
        }

    async with session.lock:
        try:
            msg = await broker.run_on_page(session.session_id, lambda p: _execute_sync(p, action))
        except PWTimeoutError as e:
            raise ActionExecutionError(f"Timeout while running {action.action}: {e}") from e
        except ActionExecutionError:
            raise
        except Exception as e:
            raise ActionExecutionError(f"{action.action} failed: {e}") from e

    shot = await take_screenshot(session, task_id, action.action)
    return {"message": msg, "screenshot": shot}
