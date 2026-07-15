"""End-to-end runner tests against the fixture app: every allowlisted action
is exercised through the real broker + worker thread + headless Chromium."""
import re

import pytest

from app.browser.runner import ActionExecutionError, execute_action, observe
from app.config import settings
from app.models.schemas import (
    AssertFieldValueAction,
    AssertTextAction,
    AssertUrlContainsAction,
    ClickElementAction,
    ClickRoleAction,
    ClickTextAction,
    FillLabelAction,
    GotoAction,
    PressKeyAction,
    ScrollAction,
    SelectOptionAction,
    TypeTextAction,
    UploadFileAction,
    WaitForTextAction,
)


TASK = "t-actions"


def _element_id(ctx, needle: str) -> int:
    for entry in ctx.interactive_elements:
        m = re.match(r"^\[(\d+)\]", entry)
        if m and needle in entry:
            return int(m.group(1))
    raise AssertionError(f"{needle!r} not found in {ctx.interactive_elements}")


# ---------- clicking ----------

async def test_click_element_by_observed_id(session_factory):
    session = await session_factory("form.html")
    ctx = await observe(session, TASK)
    save_id = _element_id(ctx, "Save")

    out = await execute_action(session, TASK, ClickElementAction(element_id=save_id))
    assert "Clicked element" in out["message"]
    # Empty form + Save → the app's required-field validation message.
    await execute_action(session, TASK, AssertTextAction(text="First name is required"))


async def test_click_element_stale_id_explains_reobserve(session_factory):
    session = await session_factory("form.html")
    await observe(session, TASK)
    with pytest.raises(ActionExecutionError, match="re-observe|Re-observe"):
        await execute_action(session, TASK, ClickElementAction(element_id=9999))


async def test_click_text_and_role(session_factory):
    session = await session_factory("form.html")
    await execute_action(session, TASK, ClickTextAction(text="Save"))
    await execute_action(session, TASK, ClickRoleAction(role="button", name="Save"))


async def test_click_text_missing_gives_actionable_error(session_factory):
    session = await session_factory("form.html")
    with pytest.raises(ActionExecutionError, match="no clickable element matched"):
        await execute_action(session, TASK, ClickTextAction(text="Nonexistent button"))


# ---------- forms ----------

async def test_fill_select_submit_and_assert_field_value(session_factory):
    session = await session_factory("form.html")
    await execute_action(session, TASK, FillLabelAction(label="First name", value="Ana"))
    await execute_action(session, TASK, SelectOptionAction(label="Country", value="France"))
    await execute_action(session, TASK, AssertFieldValueAction(label="First name", value="Ana"))
    await execute_action(session, TASK, ClickTextAction(text="Save"))
    await execute_action(session, TASK, AssertTextAction(text="Saved successfully"))


async def test_assert_field_value_mismatch_reports_actual(session_factory):
    session = await session_factory("form.html")
    await execute_action(session, TASK, FillLabelAction(label="First name", value="Ana"))
    with pytest.raises(ActionExecutionError, match="holds 'Ana'"):
        await execute_action(
            session, TASK, AssertFieldValueAction(label="First name", value="Bob")
        )


async def test_invalid_email_validation_detected(session_factory):
    session = await session_factory("form.html")
    await execute_action(session, TASK, FillLabelAction(label="First name", value="Ana"))
    await execute_action(session, TASK, FillLabelAction(label="Email", value="not-an-email"))
    await execute_action(session, TASK, ClickTextAction(text="Save"))
    await execute_action(session, TASK, AssertTextAction(text="Invalid email address"))


async def test_assert_url_contains_after_redirect(session_factory):
    session = await session_factory("form.html")
    await execute_action(session, TASK, FillLabelAction(label="First name", value="Ana"))
    await execute_action(session, TASK, ClickTextAction(text="Save and continue"))
    await execute_action(session, TASK, AssertUrlContainsAction(text="form_ok"))
    await execute_action(session, TASK, WaitForTextAction(text="Record created"))


async def test_assert_url_contains_failure(session_factory):
    session = await session_factory("form.html")
    with pytest.raises(ActionExecutionError, match="does not contain"):
        await execute_action(
            session, TASK, AssertUrlContainsAction(text="/dashboard", timeout_ms=500)
        )


# ---------- chat flow (the LinkedIn-style scenario) ----------

async def test_chat_search_open_type_and_send(session_factory):
    """Find a person buried in a long list via search, open the chat, type in
    the contenteditable composer and send — the exact flow that used to fail."""
    session = await session_factory("chat.html")

    # Search box filters the list.
    await execute_action(
        session, TASK, TypeTextAction(target="Buscar mensajes", value="Nadia")
    )
    await execute_action(session, TASK, ClickTextAction(text="Nadia Fernandez"))
    await execute_action(session, TASK, AssertTextAction(text="Chat con Nadia Fernandez"))

    # Composer is a contenteditable; Send only enables after real input events.
    await execute_action(
        session, TASK, TypeTextAction(target="Escribe un mensaje", value="Hola, ¿cómo estás?")
    )
    await execute_action(session, TASK, ClickRoleAction(role="button", name="Enviar"))
    await execute_action(session, TASK, AssertTextAction(text="Hola, ¿cómo estás?"))


async def test_chat_scroll_reaches_person_below_fold(session_factory):
    """The list loads incrementally (like LinkedIn/Slack): the target person is
    NOT in the DOM until the list has been scrolled repeatedly — the agent's
    scroll-and-re-observe workflow, exercised deterministically."""
    session = await session_factory("chat.html")
    ctx = await observe(session, TASK)
    assert "Nadia Fernandez" not in ctx.visible_text, "fixture should bury Nadia below the fold"

    for _ in range(4):
        out = await execute_action(
            session, TASK, ScrollAction(target="Persona 3", direction="bottom")
        )
        assert "Scrolled" in out["message"]
        ctx = await observe(session, TASK)
        if "Nadia Fernandez" in ctx.visible_text:
            break
    assert "Nadia Fernandez" in ctx.visible_text

    await execute_action(session, TASK, ClickTextAction(text="Nadia Fernandez"))
    await execute_action(session, TASK, AssertTextAction(text="Chat con Nadia Fernandez"))


# ---------- modal ----------

async def test_escape_closes_modal(session_factory):
    session = await session_factory("modal.html")
    ctx = await observe(session, TASK)
    assert ctx.visible_text.startswith("[OPEN DIALOG/OVERLAY")

    await execute_action(session, TASK, PressKeyAction(key="Escape"))
    ctx = await observe(session, TASK)
    assert not ctx.visible_text.startswith("[OPEN DIALOG/OVERLAY")


# ---------- uploads ----------

async def test_upload_file_from_uploads_dir(session_factory, uploads_dir):
    (uploads_dir / "informe.txt").write_text("hello")
    session = await session_factory("upload.html")

    out = await execute_action(
        session, TASK, UploadFileAction(target="Adjuntar", paths=["informe.txt"])
    )
    assert "Uploaded 1 file(s)" in out["message"]
    await execute_action(session, TASK, AssertTextAction(text="Subido: informe.txt"))


async def test_upload_outside_uploads_dir_is_blocked(session_factory, uploads_dir):
    session = await session_factory("upload.html")
    with pytest.raises(ActionExecutionError, match="outside the uploads directory"):
        await execute_action(
            session, TASK, UploadFileAction(target="Adjuntar", paths=["/etc/passwd"])
        )
    with pytest.raises(ActionExecutionError, match="outside the uploads directory"):
        await execute_action(
            session, TASK,
            UploadFileAction(target="Adjuntar", paths=["../../../etc/passwd"]),
        )


async def test_upload_missing_file_is_rejected(session_factory, uploads_dir):
    session = await session_factory("upload.html")
    with pytest.raises(ActionExecutionError, match="does not exist"):
        await execute_action(
            session, TASK, UploadFileAction(target="Adjuntar", paths=["nope.pdf"])
        )


# ---------- goto domain allowlist ----------

async def test_goto_blocked_outside_allowed_domains(session_factory, monkeypatch):
    session = await session_factory("form.html")
    monkeypatch.setattr(settings, "allowed_domains", "uat.example.com")
    with pytest.raises(ActionExecutionError, match="not in the allowed domains"):
        await execute_action(
            session, TASK, GotoAction(url="http://evil.example.net/phish")
        )


async def test_goto_allowed_when_list_empty(session_factory, fixture_server):
    session = await session_factory("form.html")
    out = await execute_action(session, TASK, GotoAction(url=f"{fixture_server}/chat.html"))
    assert "Navigated" in out["message"]
