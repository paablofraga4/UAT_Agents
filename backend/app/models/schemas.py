from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional, Union

from pydantic import BaseModel, Field


# ---------- Sessions ----------

class SessionStatus(str, Enum):
    CREATED = "created"
    WAITING_LOGIN = "waiting_login"
    AUTHENTICATED = "authenticated"
    RUNNING = "running"
    CLOSED = "closed"
    ERROR = "error"


class CreateSessionRequest(BaseModel):
    url: str


class SessionInfo(BaseModel):
    session_id: str
    url: str
    status: SessionStatus
    created_at: datetime
    last_screenshot: Optional[str] = None


# ---------- Actions (allowlist) ----------
# A Plan is a sequence of strongly-typed actions. The Planner cannot emit anything
# outside this union, and the Browser Runner enforces it again at execution time.

class GotoAction(BaseModel):
    action: Literal["goto"] = "goto"
    url: str
    description: str = ""


class ClickTextAction(BaseModel):
    action: Literal["click_text"] = "click_text"
    text: str
    description: str = ""


class ClickRoleAction(BaseModel):
    action: Literal["click_role"] = "click_role"
    role: str  # e.g. "button", "link", "tab"
    name: str
    description: str = ""


class FillLabelAction(BaseModel):
    action: Literal["fill_label"] = "fill_label"
    label: str
    value: str
    description: str = ""


class FillPlaceholderAction(BaseModel):
    action: Literal["fill_placeholder"] = "fill_placeholder"
    placeholder: str
    value: str
    description: str = ""


class SelectOptionAction(BaseModel):
    action: Literal["select_option"] = "select_option"
    label: str  # the <select> label
    value: str  # the option's visible text
    description: str = ""


class PressKeyAction(BaseModel):
    action: Literal["press_key"] = "press_key"
    key: str  # e.g. "Enter", "Tab"
    description: str = ""


class WaitAction(BaseModel):
    action: Literal["wait"] = "wait"
    ms: int = Field(ge=0, le=15000)
    description: str = ""


class WaitForTextAction(BaseModel):
    action: Literal["wait_for_text"] = "wait_for_text"
    text: str
    timeout_ms: int = Field(default=10000, ge=0, le=30000)
    description: str = ""


class ScreenshotAction(BaseModel):
    action: Literal["screenshot"] = "screenshot"
    description: str = ""


class AssertTextAction(BaseModel):
    action: Literal["assert_text"] = "assert_text"
    text: str
    description: str = ""


class ExtractContextAction(BaseModel):
    action: Literal["extract_context"] = "extract_context"
    description: str = ""


Action = Union[
    GotoAction,
    ClickTextAction,
    ClickRoleAction,
    FillLabelAction,
    FillPlaceholderAction,
    SelectOptionAction,
    PressKeyAction,
    WaitAction,
    WaitForTextAction,
    ScreenshotAction,
    AssertTextAction,
    ExtractContextAction,
]


class Plan(BaseModel):
    interpreted_task: str
    steps: list[Action]


# ---------- Step results ----------

class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class StepResult(BaseModel):
    index: int
    action: dict
    status: StepStatus
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    screenshot: Optional[str] = None
    message: Optional[str] = None
    error: Optional[str] = None


# ---------- Tasks ----------

class TaskStatus(str, Enum):
    QUEUED = "queued"
    PLANNING = "planning"
    EXECUTING = "executing"
    VALIDATING = "validating"
    REPORTING = "reporting"
    COMPLETED = "completed"
    FAILED = "failed"


class CreateTaskRequest(BaseModel):
    session_id: str
    instruction: str


class TaskInfo(BaseModel):
    task_id: str
    session_id: str
    instruction: str
    status: TaskStatus
    created_at: datetime
    plan: Optional[Plan] = None
    steps: list[StepResult] = []
    report_path: Optional[str] = None
    success: Optional[bool] = None
    summary: Optional[str] = None


# ---------- Page context (Observer → Planner) ----------

class PageContext(BaseModel):
    url: str
    title: str
    visible_text: str  # truncated
    interactive_elements: list[str]  # AX-tree-derived short labels
    screenshot_path: Optional[str] = None
