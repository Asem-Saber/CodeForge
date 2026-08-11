import io
import pytest
from rich.console import Console

import src.sandbox.paths as paths_module
from src.service import Session
from src.service.events import (
    ApprovalRequest,
    ApprovalRequested,
    AssistantMessage,
    AssistantToken,
    CodeGenerated,
    EditPreview,
    RunFinished,
    SessionStats,
    ToolCallStarted,
    ToolResult,
)
from src.ui.console import ConsoleUI, clip, format_tokens, language_for, unified_diff
from src.ui.commands import handle_command, is_command


@pytest.fixture(autouse=True)
def tmp_workspace_root(tmp_path, monkeypatch):
    monkeypatch.setattr(paths_module, "WORKSPACE_ROOT", tmp_path)
    return tmp_path


@pytest.fixture
def ui():
    buffer = io.StringIO()
    console = Console(file=buffer, width=100, force_terminal=False, no_color=True)
    return ConsoleUI(console=console), buffer


class TestHelpers:
    def test_language_from_suffix(self):
        assert language_for("a.py") == "python"
        assert language_for("a.ts") == "typescript"

    def test_language_falls_back(self):
        assert language_for("a.unknown") == "text"
        assert language_for(None) == "text"

    def test_clip_keeps_short_text(self):
        assert clip("a\nb", 5) == "a\nb"

    def test_clip_truncates_and_counts(self):
        result = clip("\n".join(str(i) for i in range(10)), 3)
        assert result.startswith("0\n1\n2")
        assert "7 more line(s)" in result

    def test_format_tokens(self):
        assert format_tokens(999) == "999"
        assert format_tokens(4200) == "4.2k"

    def test_unified_diff(self):
        preview = EditPreview(filename="a.py", before="x = 1\n", after="x = 2\n", is_new=False)
        diff = unified_diff(preview)
        assert "-x = 1" in diff
        assert "+x = 2" in diff


class TestEventRendering:
    def test_assistant_tokens_are_flushed(self, ui):
        console_ui, buffer = ui
        console_ui.handle(AssistantToken(text="hello "))
        console_ui.handle(AssistantToken(text="world"))
        console_ui.handle(RunFinished(reason="completed", stats=SessionStats(token_budget=100)))
        assert "hello world" in buffer.getvalue()

    def test_assistant_message(self, ui):
        console_ui, buffer = ui
        console_ui.handle(AssistantMessage(content="all done"))
        assert "all done" in buffer.getvalue()

    def test_tool_call(self, ui):
        console_ui, buffer = ui
        console_ui.handle(ToolCallStarted(tool_call_id="1", name="edit_file", args={"filename": "a.py"}))
        output = buffer.getvalue()
        assert "edit_file" in output
        assert "a.py" in output

    def test_execution_result_shows_output_and_exit_code(self, ui):
        console_ui, buffer = ui
        console_ui.handle(ToolResult(
            tool_call_id="1",
            name="run_sandboxed_code",
            content="{}",
            ok=True,
            data={"stdout": "42", "stderr": "", "exit_code": 0},
        ))
        output = buffer.getvalue()
        assert "42" in output
        assert "exit 0" in output

    def test_failed_result_is_shown(self, ui):
        console_ui, buffer = ui
        console_ui.handle(ToolResult(
            tool_call_id="1",
            name="run_sandboxed_code",
            content="{}",
            ok=False,
            data={"stdout": "", "stderr": "NameError: x", "exit_code": 1},
        ))
        output = buffer.getvalue()
        assert "NameError" in output
        assert "exit 1" in output

    def test_tool_call_shows_filename_not_contents(self, ui):
        console_ui, buffer = ui
        console_ui.handle(ToolCallStarted(
            tool_call_id="1",
            name="edit_file",
            args={"filename": "a.py", "find_str": "", "replace_str": "x = 1\n" * 200},
        ))
        output = buffer.getvalue()
        assert "a.py" in output
        assert "x = 1" not in output

    def test_long_args_are_summarized(self, ui):
        console_ui, buffer = ui
        console_ui.handle(ToolCallStarted(tool_call_id="1", name="validate_imports", args={"code": "y" * 300}))
        output = buffer.getvalue()
        assert "chars" in output
        assert "yyyy" not in output

    def test_code_generated_is_not_echoed(self, ui):
        console_ui, buffer = ui
        console_ui.handle(CodeGenerated(filename="a.py", language="python", code="x = 1"))
        assert buffer.getvalue().strip() == ""

    def test_simple_result_line(self, ui):
        console_ui, buffer = ui
        console_ui.handle(ToolResult(tool_call_id="1", name="edit_file", content="Success: File created.", ok=True))
        assert "Success: File created." in buffer.getvalue()

    def test_status_line_on_completion(self, ui):
        console_ui, buffer = ui
        console_ui.handle(RunFinished(
            reason="completed",
            stats=SessionStats(turn_count=3, max_turns=20, total_tokens_used=4200, token_budget=100000),
        ))
        output = buffer.getvalue()
        assert "turn 3/20" in output
        assert "4.2k/100.0k tokens" in output

    def test_retry_shown_when_retrying(self, ui):
        console_ui, buffer = ui
        console_ui.handle(RunFinished(
            reason="completed",
            stats=SessionStats(retry_count=2, max_retries=3, token_budget=100),
        ))
        assert "retry 2/3" in buffer.getvalue()

    def test_every_event_type_renders(self, ui):
        console_ui, _ = ui
        events = [
            AssistantToken(text="x"),
            AssistantMessage(content="y"),
            ToolCallStarted(tool_call_id="1", name="t", args={}),
            ToolResult(tool_call_id="1", name="t", content="ok"),
            CodeGenerated(filename="a.py", language="python"),
            ApprovalRequested(requests=[]),
            RunFinished(reason="completed", stats=SessionStats(token_budget=1)),
        ]
        for event in events:
            console_ui.handle(event)  # must not raise


class TestApprovalRendering:
    def test_new_file_shows_content(self, ui, monkeypatch):
        console_ui, buffer = ui
        monkeypatch.setattr("src.ui.console.Confirm.ask", lambda *a, **k: True)
        session = Session()
        request = ApprovalRequest("1", "edit_file", {"filename": "a.py", "find_str": "", "replace_str": "x = 1"}, "create a.py")

        assert console_ui.ask_approval([request], session) is True
        output = buffer.getvalue()
        assert "create a.py" in output
        assert "x = 1" in output

    def test_existing_file_shows_diff(self, ui, monkeypatch):
        console_ui, buffer = ui
        monkeypatch.setattr("src.ui.console.Confirm.ask", lambda *a, **k: False)
        session = Session()
        session.workspace.joinpath("a.py").write_text("x = 1\n")
        request = ApprovalRequest("1", "edit_file", {"filename": "a.py", "find_str": "x = 1", "replace_str": "x = 2"}, "modify a.py")

        assert console_ui.ask_approval([request], session) is False
        output = buffer.getvalue()
        assert "-x = 1" in output
        assert "+x = 2" in output

    def test_warns_when_edit_will_not_apply(self, ui, monkeypatch):
        console_ui, buffer = ui
        monkeypatch.setattr("src.ui.console.Confirm.ask", lambda *a, **k: False)
        session = Session()
        session.workspace.joinpath("a.py").write_text("x = 1\n")
        request = ApprovalRequest("1", "edit_file", {"filename": "a.py", "find_str": "zzz", "replace_str": "y"}, "modify a.py")

        console_ui.ask_approval([request], session)
        assert "will fail" in buffer.getvalue()

    def test_titles_multiple_actions(self, ui, monkeypatch):
        console_ui, buffer = ui
        monkeypatch.setattr("src.ui.console.Confirm.ask", lambda *a, **k: True)
        session = Session()
        requests = [
            ApprovalRequest("1", "edit_file", {"filename": "a.py", "find_str": "", "replace_str": "x"}, "create a.py"),
            ApprovalRequest("2", "run_sandboxed_code", {"filename": "a.py"}, "run a.py"),
        ]
        console_ui.ask_approval(requests, session)
        assert "2 actions" in buffer.getvalue()


class TestCommands:
    def test_detects_commands(self):
        assert is_command("/files") is True
        assert is_command("write a script") is False

    def test_help_lists_commands(self, ui):
        console_ui, buffer = ui
        result = handle_command("/help", Session(), console_ui)
        assert result.handled is True
        assert "/sessions" in buffer.getvalue()

    def test_exit(self, ui):
        console_ui, _ = ui
        assert handle_command("/exit", Session(), console_ui).should_exit is True

    def test_files_lists_workspace(self, ui):
        console_ui, buffer = ui
        session = Session()
        session.workspace.joinpath("a.py").write_text("x")
        handle_command("/files", session, console_ui)
        assert "a.py" in buffer.getvalue()

    def test_files_when_empty(self, ui):
        console_ui, buffer = ui
        handle_command("/files", Session(), console_ui)
        assert "empty" in buffer.getvalue().lower()

    def test_cost_shows_stats(self, ui):
        console_ui, buffer = ui
        handle_command("/cost", Session(), console_ui)
        assert "tokens" in buffer.getvalue()

    def test_new_swaps_session(self, ui):
        console_ui, _ = ui
        original = Session()
        result = handle_command("/new", original, console_ui)
        assert result.session is not None
        assert result.session.session_id != original.session_id

    def test_unknown_command(self, ui):
        console_ui, buffer = ui
        handle_command("/nope", Session(), console_ui)
        assert "Unknown command" in buffer.getvalue()
