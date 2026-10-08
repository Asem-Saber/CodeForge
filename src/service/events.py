from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class SessionStats:
    turn_count: int = 0
    max_turns: int = 0
    total_tokens_used: int = 0
    token_budget: int = 0
    retry_count: int = 0
    max_retries: int = 0
    has_recent_error: bool = False


@dataclass
class ApprovalRequest:
    tool_call_id: str
    tool: str
    args: dict = field(default_factory=dict)
    description: str = ""


@dataclass
class EditPreview:
    """Before/after text for a pending edit, for the caller to diff and display."""
    filename: str
    before: str
    after: str
    is_new: bool
    applies: bool = True


@dataclass
class SessionInfo:
    session_id: str
    files: list = field(default_factory=list)
    has_workspace: bool = False
    updated_at: datetime | None = None


# Streamed events

@dataclass
class Event:
    pass


@dataclass
class AssistantToken(Event):
    """A chunk of assistant text. One chunk per message if the model isn't streaming."""
    text: str
    message_id: str = ""


@dataclass
class AssistantMessage(Event):
    """A complete assistant message that did not arrive as tokens (node-authored)."""
    content: str
    message_id: str = ""


@dataclass
class ToolCallStarted(Event):
    tool_call_id: str
    name: str
    args: dict = field(default_factory=dict)


@dataclass
class ToolResult(Event):
    tool_call_id: str
    name: str
    content: str
    ok: bool = True
    data: dict | None = None


@dataclass
class CodeGenerated(Event):
    filename: str | None
    language: str
    code: str | None = None
    execution_id: str | None = None


@dataclass
class ApprovalRequested(Event):
    requests: list


@dataclass
class RunFinished(Event):
    reason: str
    stats: SessionStats = field(default_factory=SessionStats)
