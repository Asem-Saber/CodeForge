"""Headless API over the agent graph.

Drives a session, streams structured events, and answers approval pauses.
Contains no rendering — see src/ui for that.
"""
import json
import logging
from typing import Iterator

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command

from src.config import MAX_TURNS, MAX_RETRIES, TOKEN_BUDGET
from src.agent.graph import app, checkpointer, new_session_id, initial_state
from src.sandbox.manager import close_sandbox
import src.sandbox.paths as paths
from src.sandbox.paths import safe_path, session_workspace, normalize_session_id
from src.service.events import (
    ApprovalRequest,
    ApprovalRequested,
    AssistantMessage,
    AssistantToken,
    CodeGenerated,
    EditPreview,
    Event,
    RunFinished,
    SessionInfo,
    SessionStats,
    ToolCallStarted,
    ToolResult,
)

logger = logging.getLogger("codeforge")

# Tool results that are failures despite the tool returning normally.
_FAILURE_PREFIXES = ("Error", "BLOCKED", "CANCELLED", "SKIPPED")


class Session:
    """One conversation thread: its history, workspace, and sandbox."""

    def __init__(self, session_id: str | None = None):
        self.session_id = normalize_session_id(session_id) if session_id else new_session_id()
        self._config = {"configurable": {"thread_id": self.session_id}}

    # Running

    def send(self, user_input: str) -> Iterator[Event]:
        """Start a turn. Streams until the run finishes or pauses for approval."""
        logger.info("Session %s | User: %s", self.session_id, user_input[:80])
        yield from self._run(initial_state(user_input))

    def resume(self, decision) -> Iterator[Event]:
        """Answer a pending approval and continue the run.

        `decision` may be a bool, a yes/no string, or a {tool_call_id: bool} map.
        """
        if not self.is_paused():
            raise RuntimeError(f"Session {self.session_id} is not awaiting approval.")
        yield from self._run(Command(resume=decision))

    def _run(self, stream_input) -> Iterator[Event]:
        streamed_ids: set[str] = set()

        for mode, chunk in app.stream(
            stream_input, config=self._config, stream_mode=["messages", "updates"]
        ):
            if mode == "messages":
                event = self._token_event(chunk, streamed_ids)
                if event is not None:
                    yield event
            else:
                yield from self._update_events(chunk, streamed_ids)

        pending = self.pending_approval()
        if pending:
            yield ApprovalRequested(requests=pending)
            yield RunFinished(reason="awaiting_approval", stats=self.stats())
        else:
            yield RunFinished(reason="completed", stats=self.stats())

    def _token_event(self, chunk, streamed_ids: set[str]) -> Event | None:
        message, _metadata = chunk
        # "messages" mode streams tool output too; only model text is a token.
        if not isinstance(message, AIMessage):
            return None
        text = _text_of(message)
        if not text:
            return None
        message_id = getattr(message, "id", "") or ""
        streamed_ids.add(message_id)
        return AssistantToken(text=text, message_id=message_id)

    def _update_events(self, chunk: dict, streamed_ids: set[str]) -> Iterator[Event]:
        for node_name, state_update in chunk.items():
            if not isinstance(state_update, dict):
                continue  # e.g. the __interrupt__ marker

            for message in state_update.get("messages", []):
                yield from _message_events(message, streamed_ids)

            for entry in state_update.get("generated_code", []) or []:
                yield CodeGenerated(
                    filename=entry.get("filename"),
                    language=entry.get("language", "python"),
                    code=entry.get("code"),
                    execution_id=entry.get("execution_id"),
                )

    # State

    def _snapshot(self):
        return app.get_state(self._config)

    def is_paused(self) -> bool:
        return self.pending_approval() is not None

    def pending_approval(self) -> list[ApprovalRequest] | None:
        """The approval requests blocking this session, or None if it isn't paused."""
        payload = _first_interrupt_value(self._snapshot())
        if not isinstance(payload, dict) or payload.get("type") != "approval_request":
            return None
        return [
            ApprovalRequest(
                tool_call_id=request.get("tool_call_id", ""),
                tool=request.get("tool", ""),
                args=request.get("args", {}),
                description=request.get("description", ""),
            )
            for request in payload.get("requests", [])
        ]

    def exists(self) -> bool:
        return bool(self._snapshot().values)

    def history(self) -> list:
        return self._snapshot().values.get("messages", [])

    def stats(self) -> SessionStats:
        values = self._snapshot().values
        return SessionStats(
            turn_count=values.get("turn_count", 0),
            max_turns=MAX_TURNS,
            total_tokens_used=values.get("total_tokens_used", 0),
            token_budget=TOKEN_BUDGET,
            retry_count=values.get("retry_count", 0),
            max_retries=MAX_RETRIES,
            has_recent_error=values.get("has_recent_error", False),
        )

    # Workspace

    @property
    def workspace(self):
        return session_workspace(self.session_id)

    def files(self) -> list[str]:
        """Workspace-relative paths of every file this session has, sorted."""
        root = self.workspace
        return sorted(
            str(p.relative_to(root)).replace("\\", "/")
            for p in root.rglob("*")
            if p.is_file()
        )

    def read_file(self, path: str) -> str:
        return safe_path(path, self.session_id).read_text(encoding="utf-8")

    def preview(self, request: ApprovalRequest) -> EditPreview | None:
        """Before/after text for a pending edit so callers can show a diff."""
        if request.tool != "edit_file":
            return None

        filename = request.args.get("filename", "")
        find_str = request.args.get("find_str", "")
        replace_str = request.args.get("replace_str", "")

        try:
            target = safe_path(filename, self.session_id)
        except ValueError:
            return None

        if not target.exists():
            return EditPreview(filename=filename, before="", after=replace_str, is_new=True)

        try:
            before = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

        if find_str not in before:
            return EditPreview(
                filename=filename, before=before, after=before, is_new=False, applies=False
            )

        return EditPreview(
            filename=filename,
            before=before,
            after=before.replace(find_str, replace_str),
            is_new=False,
        )

    # Teardown

    def close(self):
        """Stop this session's sandbox. History and workspace survive."""
        close_sandbox(self.session_id)


# Module-level helpers

def list_sessions() -> list[SessionInfo]:
    """Every session the checkpointer has state for, newest checkpoint first."""
    infos: list[SessionInfo] = []
    seen: set[str] = set()

    for tup in checkpointer.list(None):
        session_id = tup.config["configurable"]["thread_id"]
        if session_id in seen:
            continue
        seen.add(session_id)

        # Resolved per call: the workspace root is patchable at runtime.
        workspace = paths.WORKSPACE_ROOT / session_id
        has_workspace = workspace.is_dir()
        files = (
            sorted(
                str(p.relative_to(workspace)).replace("\\", "/")
                for p in workspace.rglob("*")
                if p.is_file()
            )
            if has_workspace
            else []
        )
        infos.append(SessionInfo(session_id=session_id, files=files, has_workspace=has_workspace))

    return infos


def _first_interrupt_value(snapshot):
    for interrupt in getattr(snapshot, "interrupts", ()) or ():
        return interrupt.value
    for task in getattr(snapshot, "tasks", ()) or ():
        for interrupt in getattr(task, "interrupts", ()) or ():
            return interrupt.value
    return None


def _text_of(message) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    # Some providers return content as a list of blocks.
    return "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


def _message_events(message, streamed_ids: set[str]) -> Iterator[Event]:
    if isinstance(message, ToolMessage):
        content = message.content if isinstance(message.content, str) else str(message.content)
        data = None
        ok = not content.startswith(_FAILURE_PREFIXES)
        try:
            parsed = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            data = parsed
            if "exit_code" in parsed:
                ok = parsed["exit_code"] == 0
            elif "success" in parsed:
                ok = bool(parsed["success"])
        yield ToolResult(
            tool_call_id=message.tool_call_id,
            name=message.name or "",
            content=content,
            ok=ok,
            data=data,
        )
        return

    if not isinstance(message, AIMessage):
        return

    message_id = getattr(message, "id", "") or ""
    text = _text_of(message)
    if text and message_id not in streamed_ids:
        yield AssistantMessage(content=text, message_id=message_id)

    for tc in message.tool_calls or []:
        yield ToolCallStarted(
            tool_call_id=tc["id"], name=tc["name"], args=tc.get("args", {})
        )
