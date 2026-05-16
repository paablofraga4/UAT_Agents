"""Browser Runner

Mediates ALL agent → browser interaction. The agent never touches Playwright
directly. Every action is matched against the allowlisted Pydantic types and
executed through user-facing locators (`getByText`, `getByRole`, `getByLabel`,
`getByPlaceholder`) for resilient automation.

All Playwright calls go through the SessionBroker's worker thread (sync API),
which keeps Playwright off the host event loop entirely.
"""
from __future__ import annotations

import asyncio
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
    ScrollAction,
    SelectOptionAction,
    TypeTextAction,
    UploadFileAction,
    WaitAction,
    WaitForTextAction,
)
from app.storage.evidence import screenshot_path


DEFAULT_TIMEOUT_MS = 8000
SETTLE_MS = 800
SETTLE_AFTER = {"click_text", "click_role", "goto", "press_key", "select_option", "upload_file", "scroll"}
MAX_VISIBLE_TEXT = 6000
MAX_INTERACTIVE = 90  # nav is guaranteed inside this; content fills the rest


class ActionExecutionError(Exception):
    pass


# Single observation pass.
#
# Two hard requirements learned the hard way:
#  1. After a long session the SPA DOM grows huge (infinite-scrolled feeds,
#     panels left mounted). Reading document.body.innerText from the top and
#     truncating pushes the content that JUST loaded past the cliff and the
#     Pilot goes blind — so the BULK TEXT is scoped to the MAIN content region.
#  2. BUT the primary navigation (Home/Network/Jobs/Messaging/Notifications/Me)
#     lives in <header>/<nav>, OUTSIDE <main>. If we only report <main>, the
#     agent can no longer move between sections — it can't even SEE the
#     Notifications link. So the global nav is ALWAYS captured separately and
#     GUARANTEED into the observation (both as a NAV summary line in the text
#     and as reserved slots in interactive_elements), regardless of how much
#     content there is.
_OBSERVE_JS = r"""
({maxText, maxInter}) => {
    const clean = (s) => (s || '').replace(/\s+/g, ' ').trim().slice(0, 80);

    const pickRoot = () => {
        const m = document.querySelector('main, [role="main"]');
        if (m && m.innerText && m.innerText.trim().length > 40) return m;
        let best = document.body, bestArea = 0;
        document.querySelectorAll('div,section').forEach(el => {
            const cs = getComputedStyle(el);
            if ((cs.overflowY === 'auto' || cs.overflowY === 'scroll')
                && el.scrollHeight > el.clientHeight + 8) {
                const a = el.clientWidth * el.clientHeight;
                if (a > bestArea) { bestArea = a; best = el; }
            }
        });
        return best || document.body;
    };
    const root = pickRoot();

    // --- Primary navigation: ALWAYS captured, never truncated away ----------
    const navScopes = document.querySelectorAll(
        'header, nav, [role="navigation"], [role="banner"]'
    );
    const navItems = [];
    const navSeen = new Set();
    navScopes.forEach(scope => {
        scope.querySelectorAll('a,button,[role=link],[role=button],[role=tab]').forEach(el => {
            const lbl = clean(el.innerText || el.getAttribute('aria-label') || el.getAttribute('title'));
            if (!lbl) return;
            const k = el.tagName.toLowerCase() + '::' + lbl;
            if (navSeen.has(k)) return;
            navSeen.add(k);
            navItems.push(`[nav ${el.tagName.toLowerCase()}] ${lbl}`);
        });
    });
    const navList = navItems.slice(0, 30);

    // --- Open dialogs / modal overlays (often leftovers from prior tasks) ---
    const overlays = [];
    document.querySelectorAll('[role="dialog"],[aria-modal="true"]').forEach(d => {
        const r = d.getBoundingClientRect();
        if (r.width > 80 && r.height > 60) {
            overlays.push(clean(d.getAttribute('aria-label') ||
                (d.querySelector('h1,h2,h3') || {}).innerText || 'dialog'));
        }
    });

    const vh = window.innerHeight || 800, vw = window.innerWidth || 1280;
    const inViewport = (el) => {
        const r = el.getBoundingClientRect();
        return r.bottom > 0 && r.top < vh && r.right > 0 && r.left < vw
            && r.width > 0 && r.height > 0;
    };

    // --- Content interactive elements (main first, viewport-prioritised) ----
    const out = [];
    const seen = new Set(navSeen);
    const push = (kind, label, prio) => {
        const t = clean(label);
        if (!t) return;
        const key = kind + '::' + t;
        if (seen.has(key)) return;
        seen.add(key);
        out.push({s: `[${kind}] ${t}`, p: prio});
    };
    const collect = (scope, baseProb) => {
        scope.querySelectorAll('button,a,[role=button],[role=link],[role=tab],[role=menuitem]').forEach(el => {
            push(el.tagName.toLowerCase(),
                 el.innerText || el.getAttribute('aria-label') || el.getAttribute('title'),
                 baseProb + (inViewport(el) ? 0 : 1));
        });
        scope.querySelectorAll('input,textarea,select').forEach(el => {
            const id = el.getAttribute('id');
            let label = '';
            if (id) { const l = document.querySelector(`label[for="${CSS.escape(id)}"]`); if (l) label = l.innerText; }
            label = label || el.getAttribute('aria-label') || el.getAttribute('placeholder')
                  || el.getAttribute('name') || el.tagName;
            push(el.tagName.toLowerCase(), label, baseProb + (inViewport(el) ? 0 : 1));
        });
    };
    collect(root, 0);
    if (root !== document.body) collect(document.body, 4);
    out.sort((a, b) => a.p - b.p);

    // Nav is GUARANTEED; content fills whatever budget remains.
    const interactive = navList.concat(
        out.slice(0, Math.max(0, maxInter - navList.length)).map(o => o.s)
    );

    let text = (root.innerText || document.body.innerText || '').trim();
    if (text.length > maxText) text = text.slice(0, maxText) + '…[truncated]';

    const navSummary = navList.length
        ? `[PRIMARY NAV — use these to switch sections: ${
            navList.map(s => s.replace(/^\[nav [a-z]+\] /, '')).join(' | ')}]\n`
        : '';
    const overlayLine = overlays.length
        ? `[OPEN DIALOG/OVERLAY: ${overlays.join(' | ')} — close it (press Escape or click its X/Close) before continuing]\n`
        : '';
    return {
        visible_text: overlayLine + navSummary + (navSummary ? '\n' : '') + text,
        interactive_elements: interactive,
    };
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
    # Best-effort: let async content (notifications, feeds) settle. Bounded so a
    # never-idle SPA (LinkedIn polls forever) doesn't stall the observation.
    try:
        page.wait_for_load_state("networkidle", timeout=2500)
    except PWTimeoutError:
        pass
    try:
        url = page.url
    except Exception:
        url = ""
    try:
        # Throws "Execution context was destroyed" if the page is mid-navigation.
        title = page.title()
    except Exception:
        title = ""
    try:
        snap = page.evaluate(_OBSERVE_JS, {"maxText": MAX_VISIBLE_TEXT, "maxInter": MAX_INTERACTIVE}) or {}
    except Exception:
        snap = {}
    return {
        "url": url,
        "title": title,
        "visible_text": snap.get("visible_text") or "",
        "interactive_elements": snap.get("interactive_elements") or [],
    }


async def observe(session: BrowserSession, task_id: str) -> PageContext:
    # Hold the session lock so an observation can't interleave with an action
    # on the same page (two tasks may share one session).
    async with session.lock:
        snap = await broker.run_on_page(session.session_id, _observe_sync)
        shot = await take_screenshot(session, task_id, "observe")
    return PageContext(
        url=snap["url"],
        title=snap["title"],
        visible_text=snap["visible_text"],
        interactive_elements=snap["interactive_elements"],
        screenshot_path=shot,
    )


def _try_click(page: Page, locator_builders: list[tuple[str, Any]], per_try_ms: int = 1500) -> str:
    """Try a sequence of (label, locator-factory) pairs. Returns the label of the
    first one that succeeds. Each attempt: wait for visibility, then click. If the
    element is disabled (e.g. Send button before composer registers input), wait
    briefly for it to become enabled."""
    tried: list[str] = []
    for label, build in locator_builders:
        tried.append(label)
        try:
            loc = build()
            loc.wait_for(state="visible", timeout=per_try_ms)
            try:
                loc.click(timeout=per_try_ms)
            except Exception:
                # Element may be temporarily disabled (e.g. Send waiting for input
                # event from a contenteditable). Re-poll for enabled state.
                loc.click(timeout=DEFAULT_TIMEOUT_MS, force=False)
            return label
        except Exception:
            continue
    # A clear, actionable message — "Timeout 1500ms exceeded" alone told us
    # nothing about WHAT was searched for.
    raise ActionExecutionError(
        "no clickable element matched. Tried: " + "; ".join(tried)
        + ". The target text/role is probably not on the page — re-observe "
        "(extract_context), check the PRIMARY NAV summary, or scroll."
    )


def _click_strategies(page: Page, name: str, role: str | None = None) -> list[tuple[str, Any]]:
    """Build a fallback chain to find a clickable element by visible name / aria-label.
    `role` narrows the first attempts (button/link/tab/etc.); when None, we try the
    common interactive roles in order."""
    n = name.strip()
    roles = [role] if role else ["button", "link", "menuitem", "tab", "checkbox", "radio"]
    builders: list[tuple[str, Any]] = []
    for r in roles:
        builders.append((f"role={r} name={n!r}", lambda r=r: page.get_by_role(r, name=n).first))
        builders.append((f"role={r} name~={n!r}", lambda r=r: page.get_by_role(r, name=n, exact=False).first))
    # aria-label exact + substring (case-insensitive)
    builders.append((f"aria-label={n!r}", lambda: page.locator(f'[aria-label="{n}" i]:visible').first))
    builders.append((f"aria-label*={n!r}", lambda: page.locator(f'[aria-label*="{n}" i]:visible').first))
    # title attribute (icon buttons sometimes only have title)
    builders.append((f"title*={n!r}", lambda: page.locator(f'[title*="{n}" i]:visible').first))
    # text constrained to clickable elements (avoid grabbing a non-clickable span)
    builders.append((f"clickable text={n!r}", lambda: page.locator(
        f'button:has-text("{n}"), a:has-text("{n}"), [role="button"]:has-text("{n}"), '
        f'[role="link"]:has-text("{n}"), [role="tab"]:has-text("{n}"), '
        f'[role="menuitem"]:has-text("{n}")'
    ).first))
    # last resort: any visible text node (original behaviour)
    builders.append((f"text={n!r}", lambda: page.get_by_text(n, exact=False).first))
    return builders


def _resolve_file_input(page: Page, target: str):
    """Locate the underlying <input type=file>, even when hidden behind a styled
    label/button. Strategy: try by accessible name first, then sniff the input
    closest to the target text."""
    t = target.strip()
    # 1) Direct: input with matching aria-label / name / id / data-testid
    for sel in (
        f'input[type="file"][aria-label*="{t}" i]',
        f'input[type="file"][name*="{t}" i]',
        f'input[type="file"][id*="{t}" i]',
        f'input[type="file"][data-testid*="{t}" i]',
    ):
        loc = page.locator(sel).first
        if loc.count() > 0:
            return loc
    # 2) Indirect: find a clickable ancestor with matching text/aria-label,
    #    then look for the file input inside it (or its <label for=...> target).
    for parent_sel in (
        f'label:has-text("{t}")',
        f'button:has-text("{t}")',
        f'[role="button"]:has-text("{t}")',
        f'[aria-label*="{t}" i]',
    ):
        parent = page.locator(parent_sel).first
        if parent.count() == 0:
            continue
        # input inside the parent
        inner = parent.locator('input[type="file"]').first
        if inner.count() > 0:
            return inner
        # <label for="X"> → input#X
        try:
            for_id = parent.evaluate("el => el.getAttribute && el.getAttribute('for')")
            if for_id:
                ref = page.locator(f'input[type="file"]#{for_id}').first
                if ref.count() > 0:
                    return ref
        except Exception:
            pass
    # 3) Fallback: the first file input on the page
    any_input = page.locator('input[type="file"]').first
    if any_input.count() > 0:
        return any_input
    raise ActionExecutionError(f"upload_file: no <input type=file> found for {target!r}")


# Dispatch input/change events on contenteditable so frameworks (React, Lexical,
# ProseMirror, Draft.js, Quill) detect the change and enable Send buttons.
_CE_NOTIFY_JS = r"""
(el) => {
    try {
        el.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, cancelable: true, inputType: 'insertText' }));
        el.dispatchEvent(new InputEvent('input', { bubbles: true, cancelable: true, inputType: 'insertText' }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
    } catch (e) {}
}
"""


def _execute_sync(page: Page, action: Action) -> str:
    if isinstance(action, GotoAction):
        page.goto(action.url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT_MS * 2)
        return f"Navigated to {action.url}"
    if isinstance(action, ClickTextAction):
        used = _try_click(page, _click_strategies(page, action.text))
        return f"Clicked text {action.text!r} via {used}"
    if isinstance(action, ClickRoleAction):
        used = _try_click(page, _click_strategies(page, action.name, role=action.role))
        return f"Clicked {action.role}={action.name!r} via {used}"
    if isinstance(action, FillLabelAction):
        page.get_by_label(action.label, exact=False).first.fill(action.value, timeout=DEFAULT_TIMEOUT_MS)
        return f"Filled label {action.label!r}"
    if isinstance(action, FillPlaceholderAction):
        page.get_by_placeholder(action.placeholder, exact=False).first.fill(action.value, timeout=DEFAULT_TIMEOUT_MS)
        return f"Filled placeholder {action.placeholder!r}"
    if isinstance(action, TypeTextAction):
        target = action.target.replace('"', "'")  # avoid breaking the CSS selector
        # 1) Try standard real-input locators with short timeouts so we fail fast.
        for build, label in [
            (lambda: page.get_by_label(target, exact=False).first,        "label"),
            (lambda: page.get_by_placeholder(target, exact=False).first,  "placeholder"),
            (lambda: page.get_by_role("textbox", name=target).first,      "role=textbox"),
        ]:
            try:
                build().fill(action.value, timeout=1500)
                return f"Typed into {target!r} via {label}"
            except Exception:
                continue
        # 2) contenteditable matched by target text (substring, case-insensitive).
        sel_target = (
            f'[contenteditable="true"][aria-label*="{target}" i], '
            f'[contenteditable=""][aria-label*="{target}" i], '
            f'[contenteditable="true"][aria-placeholder*="{target}" i], '
            f'[contenteditable="true"][data-placeholder*="{target}" i], '
            f'[role="textbox"][aria-label*="{target}" i]'
        )
        # 3) Last-resort: ANY visible text-entry element on the page (real inputs,
        #    textareas, contenteditables, role=textbox). Prefers the focused one.
        sel_any = (
            '*:focus, '
            'input[type="text"], input[type="search"], input:not([type]), '
            'textarea, '
            '[contenteditable="true"], [contenteditable=""], [role="textbox"]'
        )

        loc = None
        for sel, src in ((sel_target, "target"), (sel_any, "any")):
            try:
                cand = page.locator(sel).first
                cand.wait_for(state="visible", timeout=2500)
                loc = cand
                used = src
                break
            except Exception:
                continue
        if loc is None:
            raise ActionExecutionError(
                f"type_text: could not find any input/contenteditable matching {target!r}"
            )

        # If it's a real <input>/<textarea>, .fill() is the most reliable path.
        try:
            tag = (loc.evaluate("el => el.tagName") or "").lower()
        except Exception:
            tag = ""
        if tag in ("input", "textarea"):
            loc.fill(action.value)
            return f"Filled {target!r} via {used}"

        # Otherwise it's a contenteditable / rich editor: click + insert_text.
        loc.click()
        try:
            page.keyboard.press("Control+A")
            page.keyboard.press("Delete")
        except Exception:
            pass
        page.keyboard.insert_text(action.value)
        # Notify frameworks (React/Lexical/ProseMirror) so Send buttons enable.
        try:
            loc.evaluate(_CE_NOTIFY_JS)
        except Exception:
            pass
        return f"Typed into contenteditable {target!r} via {used}"

    if isinstance(action, UploadFileAction):
        loc = _resolve_file_input(page, action.target)
        loc.set_input_files(action.paths)
        return f"Uploaded {len(action.paths)} file(s) into {action.target!r}"

    if isinstance(action, ScrollAction):
        # Walk UP from a known element to find the nearest scrollable ancestor
        # (O(depth), no DOM scan). The anchor is located with Playwright's
        # selector engine — never an Array.from(querySelectorAll('*')) over
        # thousands of nodes (that froze the worker on big SPAs).
        scroll_js = r"""
        (el, {direction, amount}) => {
            const findScrollable = (node) => {
                while (node && node !== document.body && node !== document.documentElement) {
                    const cs = getComputedStyle(node);
                    const oy = cs.overflowY;
                    if ((oy === 'auto' || oy === 'scroll') && node.scrollHeight > node.clientHeight + 4) {
                        return node;
                    }
                    node = node.parentElement;
                }
                return document.scrollingElement || document.documentElement;
            };
            const c = findScrollable(el);
            const before = c.scrollTop;
            if (direction === 'bottom') c.scrollTop = c.scrollHeight;
            else if (direction === 'top') c.scrollTop = 0;
            else if (direction === 'up') c.scrollTop = Math.max(0, c.scrollTop - amount);
            else c.scrollTop = c.scrollTop + amount;
            return {moved: c.scrollTop - before, top: c.scrollTop, max: c.scrollHeight};
        }
        """
        anchor = None
        if action.target:
            t = action.target.strip()
            for build in (
                lambda: page.get_by_text(t, exact=False).first,
                lambda: page.get_by_label(t, exact=False).first,
                lambda: page.locator(f'[aria-label*="{t}" i]').first,
            ):
                try:
                    cand = build()
                    cand.wait_for(state="attached", timeout=1500)
                    anchor = cand
                    break
                except Exception:
                    continue
        if anchor is None:
            # No target (or not found): scroll the page's main scrollable.
            anchor = page.locator("body")
        info = anchor.evaluate(
            scroll_js, {"direction": action.direction, "amount": action.amount}
        )
        return (
            f"Scrolled {action.direction} "
            f"(Δ={info.get('moved')}px, top={info.get('top')}/{info.get('max')})"
        )

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

        # Auto-settle after navigations / clicks: give SPAs a moment to render
        # the next view before the next observation happens.
        if action.action in SETTLE_AFTER:
            try:
                await broker.run_on_page(
                    session.session_id,
                    lambda p: p.wait_for_load_state("domcontentloaded", timeout=3000),
                )
            except Exception:
                pass
            await asyncio.sleep(SETTLE_MS / 1000)

    shot = await take_screenshot(session, task_id, action.action)
    return {"message": msg, "screenshot": shot}
