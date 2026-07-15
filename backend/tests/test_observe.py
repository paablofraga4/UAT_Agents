"""Observation contract: numbered elements, guaranteed nav, overlay warning."""
import re

from app.browser.runner import observe


NUMBERED = re.compile(r"^\[(\d+)\]\[(nav )?[a-z]+\] .+")


async def test_observe_numbers_every_interactive_element(session_factory):
    session = await session_factory("form.html")
    ctx = await observe(session, "t-observe")

    assert ctx.url.endswith("form.html")
    assert ctx.title == "Form fixture"
    assert ctx.interactive_elements, "expected interactive elements"
    for entry in ctx.interactive_elements:
        assert NUMBERED.match(entry), f"not numbered: {entry!r}"

    # Ids must be unique — click_element depends on it.
    ids = [int(NUMBERED.match(e).group(1)) for e in ctx.interactive_elements]
    assert len(ids) == len(set(ids))


async def test_observe_guarantees_primary_nav(session_factory):
    session = await session_factory("form.html")
    ctx = await observe(session, "t-nav")

    assert "[PRIMARY NAV" in ctx.visible_text
    assert "Chat" in ctx.visible_text.split("\n")[0] or "Chat" in ctx.visible_text
    nav_entries = [e for e in ctx.interactive_elements if "[nav " in e]
    assert nav_entries, "persistent nav must be reported"


async def test_observe_flags_open_dialog(session_factory):
    session = await session_factory("modal.html")
    ctx = await observe(session, "t-modal")

    assert ctx.visible_text.startswith("[OPEN DIALOG/OVERLAY")
    assert "Cookie consent" in ctx.visible_text


async def test_observe_reports_form_fields(session_factory):
    session = await session_factory("form.html")
    ctx = await observe(session, "t-fields")

    labels = " ".join(ctx.interactive_elements)
    for expected in ("First name", "Email", "Country", "Save"):
        assert expected in labels, f"{expected!r} missing from {labels}"
