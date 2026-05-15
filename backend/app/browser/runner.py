"""Browser Runner

Mediates ALL agent → browser interaction. The agent never touches the Playwright
Page directly. Every action is matched against the allowlisted Pydantic types and
executed through user-facing locators (`getByText`, `getByRole`, `getByLabel`,
`getByPlaceholder`) for resilient automation.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from playwright.async_api import Page, TimeoutError as PWTimeoutError

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


async def take_screenshot(session: BrowserSession, task_id: str, label: str) -> str:
    path = screenshot_path(session.session_id, task_id, label)
    try:
        await session.page.screenshot(path=str(path), full_page=False)
    except Exception as e:
        raise ActionExecutionError(f"screenshot failed: {e}") from e
    session.last_screenshot = str(path)
    return str(path)


async def observe(session: BrowserSession, task_id: str) -> PageContext:
    """Build a structured page snapshot for the LLM."""
    page: Page = session.page
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
    except PWTimeoutError:
        pass

    url = page.url
    title = await page.title()

    try:
        body_text = await page.evaluate(
            """() => document.body ? document.body.innerText : ''"""
        )
    except Exception:
        body_text = ""
    body_text = (body_text or "")[:MAX_VISIBLE_TEXT]

    interactive: list[str] = []
    try:
        interactive = await page.evaluate(
            """() => {
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
            }"""
        )
    except Exception:
        interactive = []
    interactive = interactive[:MAX_INTERACTIVE]

    shot = await take_screenshot(session, task_id, "observe")

    return PageContext(
        url=url,
        title=title,
        visible_text=body_text,
        interactive_elements=interactive,
        screenshot_path=shot,
    )


async def execute_action(
    session: BrowserSession, task_id: str, action: Action
) -> dict[str, Any]:
    """Run a single allowlisted action. Returns a result dict with screenshot + msg."""
    page: Page = session.page
    msg = ""

    async with session.lock:
        try:
            if isinstance(action, GotoAction):
                await page.goto(action.url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT_MS * 2)
                msg = f"Navigated to {action.url}"

            elif isinstance(action, ClickTextAction):
                await page.get_by_text(action.text, exact=False).first.click(timeout=DEFAULT_TIMEOUT_MS)
                msg = f"Clicked text {action.text!r}"

            elif isinstance(action, ClickRoleAction):
                await page.get_by_role(action.role, name=action.name).first.click(timeout=DEFAULT_TIMEOUT_MS)
                msg = f"Clicked {action.role}={action.name!r}"

            elif isinstance(action, FillLabelAction):
                await page.get_by_label(action.label, exact=False).first.fill(action.value, timeout=DEFAULT_TIMEOUT_MS)
                msg = f"Filled label {action.label!r}"

            elif isinstance(action, FillPlaceholderAction):
                await page.get_by_placeholder(action.placeholder, exact=False).first.fill(
                    action.value, timeout=DEFAULT_TIMEOUT_MS
                )
                msg = f"Filled placeholder {action.placeholder!r}"

            elif isinstance(action, SelectOptionAction):
                locator = page.get_by_label(action.label, exact=False).first
                await locator.select_option(label=action.value, timeout=DEFAULT_TIMEOUT_MS)
                msg = f"Selected {action.value!r} in {action.label!r}"

            elif isinstance(action, PressKeyAction):
                await page.keyboard.press(action.key)
                msg = f"Pressed {action.key}"

            elif isinstance(action, WaitAction):
                await asyncio.sleep(action.ms / 1000)
                msg = f"Waited {action.ms}ms"

            elif isinstance(action, WaitForTextAction):
                await page.get_by_text(action.text, exact=False).first.wait_for(
                    state="visible", timeout=action.timeout_ms
                )
                msg = f"Text {action.text!r} appeared"

            elif isinstance(action, ScreenshotAction):
                msg = "Screenshot captured"

            elif isinstance(action, AssertTextAction):
                locator = page.get_by_text(action.text, exact=False).first
                await locator.wait_for(state="visible", timeout=DEFAULT_TIMEOUT_MS)
                msg = f"Asserted text {action.text!r} visible"

            elif isinstance(action, ExtractContextAction):
                ctx = await observe(session, task_id)
                return {
                    "message": "Extracted page context",
                    "screenshot": ctx.screenshot_path,
                    "context": ctx.model_dump(),
                }

            else:
                raise ActionExecutionError(f"Unsupported action: {type(action).__name__}")

        except PWTimeoutError as e:
            raise ActionExecutionError(f"Timeout while running {action.action}: {e}") from e
        except Exception as e:
            raise ActionExecutionError(f"{action.action} failed: {e}") from e

    shot = await take_screenshot(session, task_id, action.action)
    return {"message": msg, "screenshot": shot}
