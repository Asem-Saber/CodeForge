import io
import pytest
from rich.console import Console

import main
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
from src.ui.console import (
    ConsoleUI,
    clip,
    clip_line,
    format_tokens,
    language_for,
    last_line,
    result_glyph,
    strip_code_fences,
    unified_diff,
)
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


class TestStripCodeFences:
    def test_block_becomes_a_reference_line(self):
        text = "here:\n\n```python\nx = 1\ny = 2\n```\n\ndone"
        result = strip_code_fences(text)
        assert "x = 1" not in result
        assert "▸ python (2 lines)" in result
        assert "here:" in result
        assert "done" in result

    def test_filenames_are_consumed_in_order(self):
        text = "```python\na\n```\n```python\nb\nc\n```"
        result = strip_code_fences(text, ["first.py", "second.py"])
        assert "▸ first.py (1 line)" in result
        assert "▸ second.py (2 lines)" in result

    def test_falls_back_to_language_then_code(self):
        assert "▸ python (1 line)" in strip_code_fences("```python\na\n```")
        assert "▸ code (1 line)" in strip_code_fences("```\na\n```")

    def test_count_noun_agrees(self):
        assert strip_code_fences("```\na\n```") == "▸ code (1 line)"
        assert strip_code_fences("```\na\nb\n```") == "▸ code (2 lines)"

    def test_crlf_fence_is_collapsed(self):
        result = strip_code_fences("hi\r\n```python\r\nx = 1\r\ny = 2\r\n```\r\ndone")
        assert "x = 1" not in result
        assert "▸ python (2 lines)" in result
        assert "done" in result

    def test_indented_fence_in_list_item_is_collapsed(self):
        result = strip_code_fences("1. step:\n  ```python\n  x = 1\n  ```\n2. next")
        assert "x = 1" not in result
        assert "▸ python (1 line)" in result
        assert "2. next" in result

    def test_tilde_fence_is_collapsed(self):
        result = strip_code_fences("~~~python\nx = 1\ny = 2\n~~~")
        assert "x = 1" not in result
        assert result == "▸ python (2 lines)"

    def test_tilde_fence_is_not_closed_by_backticks(self):
        text = "~~~python\nx = 1\n```\n"
        assert strip_code_fences(text) == text

    def test_backtick_fence_is_not_closed_by_tildes(self):
        text = "```python\nx = 1\n~~~\n"
        assert strip_code_fences(text) == text

    def test_closing_marker_with_trailing_spaces(self):
        result = strip_code_fences("```python\nx = 1\n```   \nafter")
        assert "x = 1" not in result
        assert "▸ python (1 line)" in result
        assert "after" in result

    def test_empty_fence_body(self):
        assert strip_code_fences("```python\n```") == "▸ python (0 lines)"

    def test_inline_backticks_survive(self):
        assert strip_code_fences("use `x = 1` here") == "use `x = 1` here"

    def test_unterminated_fence_is_left_alone(self):
        text = "```python\nx = 1\n"
        assert strip_code_fences(text) == text

    def test_fence_indented_four_spaces_in_list_item(self):
        text = "1. step:\n    ```python\n    x = 1\n    ```\n2. next"
        result = strip_code_fences(text)
        assert "x = 1" not in result
        assert "2. next" in result
        assert "▸ python (1 line)" in result

    def test_fence_indented_eight_spaces_in_nested_bullet(self):
        text = "- outer:\n  - inner:\n        ```python\n        x = 1\n        ```"
        result = strip_code_fences(text)
        assert "x = 1" not in result
        assert "▸ python (1 line)" in result

    def test_indented_reference_line_preserves_indentation(self):
        text = "1. step:\n    ```python\n    x = 1\n    ```"
        result = strip_code_fences(text)
        lines = result.split('\n')
        assert len(lines) >= 2
        assert lines[1].startswith("    ▸")


class TestProse:
    def test_tokens_do_not_print_before_the_run_finishes(self, ui):
        console_ui, buffer = ui
        console_ui.handle(AssistantToken(text="hello"))
        assert buffer.getvalue() == ""

    def test_prose_flushes_on_awaiting_approval(self, ui):
        console_ui, buffer = ui
        console_ui.handle(AssistantToken(text="about to edit a.py"))
        console_ui.handle(RunFinished(reason="awaiting_approval", stats=SessionStats(token_budget=100)))
        output = buffer.getvalue()
        assert "about to edit a.py" in output
        assert "turn " not in output
        assert "tokens" not in output

    def test_code_in_prose_is_collapsed(self, ui):
        console_ui, buffer = ui
        request = ApprovalRequest("1", "edit_file", {"filename": "fib.py", "find_str": "", "replace_str": "x = 1"})
        console_ui.handle(AssistantToken(text="done:\n\n```python\nx = 1\n```\n"))
        console_ui.handle(ApprovalRequested(requests=[request]))
        console_ui.handle(RunFinished(reason="awaiting_approval", stats=SessionStats(token_budget=100)))
        output = buffer.getvalue()
        assert "x = 1" not in output
        assert "▸ fib.py (1 line)" in output

    def test_buffer_does_not_leak_into_the_next_turn(self, ui):
        console_ui, buffer = ui
        console_ui.handle(AssistantToken(text="first turn"))
        console_ui.handle(RunFinished(reason="completed", stats=SessionStats(token_budget=100)))
        buffer.truncate(0)
        buffer.seek(0)
        console_ui.handle(RunFinished(reason="completed", stats=SessionStats(token_budget=100)))
        assert "first turn" not in buffer.getvalue()

    def test_code_files_do_not_leak_into_the_next_turn(self, ui):
        console_ui, buffer = ui
        console_ui.handle(CodeGenerated(filename="stale.py", language="python"))
        console_ui.handle(RunFinished(reason="completed", stats=SessionStats(token_budget=100)))
        buffer.truncate(0)
        buffer.seek(0)
        console_ui.handle(AssistantToken(text="```python\nx = 1\n```"))
        console_ui.handle(RunFinished(reason="completed", stats=SessionStats(token_budget=100)))
        output = buffer.getvalue()
        assert "stale.py" not in output
        assert "▸ python (1 line)" in output


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
        console_ui.handle(RunFinished(reason="completed", stats=SessionStats(token_budget=100)))
        assert "all done" in buffer.getvalue()

    def test_tool_call(self, ui):
        console_ui, buffer = ui
        args = {"filename": "a.py", "find_str": "", "replace_str": "x = 1"}
        console_ui.handle(ToolCallStarted(tool_call_id="1", name="edit_file", args=args))
        console_ui.handle(ToolResult(tool_call_id="1", name="edit_file", content="Success: File created.", ok=True))
        output = buffer.getvalue()
        assert "create" in output
        assert "a.py" in output
        assert len([line for line in output.splitlines() if line.strip()]) == 1

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
        console_ui.handle(ToolResult(tool_call_id="1", name="edit_file", content="Success: File created.", ok=True))
        output = buffer.getvalue()
        assert "a.py" in output
        assert "x = 1" not in output

    def test_long_args_are_summarized(self, ui):
        console_ui, buffer = ui
        console_ui.handle(ToolCallStarted(tool_call_id="1", name="validate_imports", args={"code": "y" * 300}))
        console_ui.handle(ToolResult(tool_call_id="1", name="validate_imports", content="ok", ok=True))
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
            console_ui.handle(event) 


class TestStatusWiring:
    def test_tool_call_no_longer_writes_to_scrollback(self, ui):
        console_ui, buffer = ui
        console_ui.handle(ToolCallStarted(tool_call_id="1", name="edit_file", args={"filename": "a.py"}))
        assert buffer.getvalue() == ""

    def test_run_finished_stops_the_region(self, ui):
        console_ui, _ = ui
        console_ui.status.start()
        console_ui.handle(RunFinished(reason="completed", stats=SessionStats(token_budget=100)))
        assert console_ui.status.is_active is False

    def test_approval_pauses_the_region(self, ui, monkeypatch):
        console_ui, _ = ui
        recorded_is_active = {}

        def capture_is_active(*a, **k):
            recorded_is_active['value'] = console_ui.status.is_active
            return True

        monkeypatch.setattr("src.ui.console.Confirm.ask", capture_is_active)
        console_ui.status.start()
        console_ui.ask_approval([], Session())
        assert recorded_is_active.get('value') is False
        assert console_ui.status.is_active is False

    def test_tokens_never_trigger_a_stats_read(self, ui):
        console_ui, _ = ui

        class ExplodingSession:
            def stats(self):
                raise AssertionError("stats() must not be called for a token")

        console_ui.handle(AssistantToken(text="hello"), ExplodingSession())

    def test_end_turn_discards_prose_from_an_abandoned_turn(self, ui):
        console_ui, buffer = ui

        def drive_like(events):
            console_ui.status.start()
            try:
                for event in events:
                    console_ui.handle(event)
            finally:
                console_ui.end_turn()

        def dying_turn():
            yield AssistantToken(text="abandoned explanation")
            yield CodeGenerated(filename="stale.py", language="python")
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            drive_like(dying_turn())
        assert console_ui.status.is_active is False

        drive_like([
            AssistantToken(text="fresh turn"),
            RunFinished(reason="completed", stats=SessionStats(token_budget=100)),
        ])
        output = buffer.getvalue()
        assert "abandoned" not in output
        assert "fresh turn" in output

    def test_drive_finally_wiring_cleans_status(self, ui):
        console_ui, buffer = ui
        session = Session()

        def events_that_raise():
            yield AssistantToken(text="start of prose")
            yield CodeGenerated(filename="a.py", language="python")
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            main.drive(events_that_raise(), session, console_ui)

        assert console_ui.status.is_active is False
        assert "start of prose" not in buffer.getvalue()

        console_ui.handle(AssistantToken(text="new turn"))
        console_ui.handle(RunFinished(reason="completed", stats=SessionStats(token_budget=100)))
        output = buffer.getvalue()
        assert "new turn" in output

    def test_tool_events_refresh_stats(self, ui):
        console_ui, buffer = ui

        class StubSession:
            def __init__(self):
                self.stats_call_count = 0

            def stats(self):
                self.stats_call_count += 1
                return SessionStats(token_budget=1000)

        session = StubSession()

        console_ui.handle(AssistantToken(text="hello"), session)
        assert session.stats_call_count == 0

        console_ui.handle(ToolCallStarted(tool_call_id="1", name="edit_file", args={"filename": "a.py"}), session)
        assert session.stats_call_count == 1

        console_ui.handle(ToolResult(tool_call_id="1", name="edit_file", content="Success", ok=True), session)
        assert session.stats_call_count == 2


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


class TestStepRows:
    def _row(self, console_ui, buffer, name, args, **result):
        console_ui.handle(ToolCallStarted(tool_call_id="1", name=name, args=args))
        console_ui.handle(ToolResult(tool_call_id="1", name=name, **result))
        return buffer.getvalue()

    def test_create_shows_added_lines(self, ui):
        console_ui, buffer = ui
        output = self._row(
            console_ui, buffer, "edit_file",
            {"filename": "a.py", "find_str": "", "replace_str": "x = 1\ny = 2\n"},
            content="Success: File created.", ok=True,
        )
        assert "create" in output
        assert "a.py" in output
        assert "+2" in output
        assert "x = 1" not in output

    def test_patch_shows_added_and_removed(self, ui):
        console_ui, buffer = ui
        output = self._row(
            console_ui, buffer, "edit_file",
            {"filename": "a.py", "find_str": "x = 1\n", "replace_str": "x = 2\ny = 3\n"},
            content="Success: File edited.", ok=True,
        )
        assert "patch" in output
        assert "+2/-1" in output

    def test_successful_run_collapses_to_one_line(self, ui):
        console_ui, buffer = ui
        output = self._row(
            console_ui, buffer, "run_sandboxed_code", {"filename": "t.py"},
            content="{}", ok=True,
            data={"stdout": "collecting ...\n5 passed in 0.31s\n", "stderr": "", "exit_code": 0},
        )
        assert "exit 0" in output
        assert "5 passed in 0.31s" in output
        assert "collecting" not in output

    def test_run_with_no_stdout_shows_only_exit(self, ui):
        console_ui, buffer = ui
        output = self._row(
            console_ui, buffer, "run_sandboxed_code", {"filename": "t.py"},
            content="{}", ok=True,
            data={"stdout": "", "stderr": "", "exit_code": 0},
        )
        assert output.rstrip().endswith("exit 0")
        assert "·" not in output

    def test_whitespace_only_stdout_shows_only_exit(self, ui):
        console_ui, buffer = ui
        output = self._row(
            console_ui, buffer, "run_sandboxed_code", {"filename": "t.py"},
            content="{}", ok=True,
            data={"stdout": "  \n\n", "stderr": "", "exit_code": 0},
        )
        assert output.rstrip().endswith("exit 0")
        assert "·" not in output

    def test_nonzero_exit_with_stdout_shows_both_and_panel(self, ui):
        console_ui, buffer = ui
        output = self._row(
            console_ui, buffer, "run_sandboxed_code", {"filename": "t.py"},
            content="{}", ok=False,
            data={"stdout": "1 failed in 0.2s", "stderr": "boom", "exit_code": 1},
        )
        row = next(line for line in output.splitlines() if "exit 1 ·" in line)
        assert "1 failed in 0.2s" in row
        assert "boom" in output
        assert "run_sandboxed_code - exit 1" in output

    def test_null_values_do_not_crash(self, ui):
        console_ui, buffer = ui
        output = self._row(
            console_ui, buffer, "edit_file",
            {"filename": "a.py", "find_str": None, "replace_str": None},
            content="Success: File created.", ok=True,
        )
        assert "+0" in output
        output = self._row(
            console_ui, buffer, "run_sandboxed_code", {"filename": "t.py"},
            content="{}", ok=True,
            data={"stdout": None, "stderr": "", "exit_code": 0},
        )
        assert "exit 0" in output

    def test_failed_edit_shows_error_not_counts(self, ui):
        console_ui, buffer = ui
        output = self._row(
            console_ui, buffer, "edit_file",
            {"filename": "a.py", "find_str": "a\nb\n", "replace_str": "x\ny\nz\n"},
            content="Error: find_str not found in file.", ok=False,
        )
        assert "Error: find_str not found in file." in output
        assert "+3/-2" not in output

    def test_failed_read_shows_error_not_line_count(self, ui):
        console_ui, buffer = ui
        output = self._row(
            console_ui, buffer, "read_file_content", {"path": "nope.py"},
            content="Error: file not found", ok=False,
        )
        assert "Error: file not found" in output
        assert "1 lines" not in output
        assert "1 line" not in output

    def test_read_single_line_is_singular(self, ui):
        console_ui, buffer = ui
        output = self._row(
            console_ui, buffer, "read_file_content", {"path": "a.py"},
            content="only", ok=True,
        )
        assert "1 line" in output
        assert "1 lines" not in output

    def test_failure_keeps_the_output_panel(self, ui):
        console_ui, buffer = ui
        output = self._row(
            console_ui, buffer, "run_sandboxed_code", {"filename": "t.py"},
            content="{}", ok=False,
            data={"stdout": "", "stderr": "NameError: x", "exit_code": 1},
        )
        assert "exit 1" in output
        assert "NameError" in output

    def test_read_shows_line_count(self, ui):
        console_ui, buffer = ui
        output = self._row(
            console_ui, buffer, "read_file_content", {"path": "a.py"},
            content="a\nb\nc", ok=True,
        )
        assert "read" in output
        assert "3 lines" in output

    def test_result_without_a_matching_call_falls_back_to_content(self, ui):
        console_ui, buffer = ui
        console_ui.handle(ToolResult(
            tool_call_id="unseen", name="edit_file", content="Success: File created.", ok=True,
        ))
        assert "Success: File created." in buffer.getvalue()

    def test_call_args_do_not_accumulate(self, ui):
        console_ui, buffer = ui
        self._row(
            console_ui, buffer, "edit_file",
            {"filename": "a.py", "find_str": "", "replace_str": "x"},
            content="Success: File created.", ok=True,
        )
        assert console_ui._call_args == {}


class TestResultGlyph:
    class _Console:
        def __init__(self, legacy_windows):
            self.legacy_windows = legacy_windows

    def test_normal_glyphs(self):
        console = self._Console(False)
        assert result_glyph(console, True) == "✓"
        assert result_glyph(console, False) == "✗"

    def test_legacy_windows_fallback(self):
        console = self._Console(True)
        assert result_glyph(console, True) == "+"
        assert result_glyph(console, False) == "!"


class TestLineHelpers:
    def test_last_line_skips_blanks(self):
        assert last_line("a\n\nb\n\n") == "b"
        assert last_line("   \n") == ""

    def test_clip_line_truncates(self):
        assert clip_line("short", 40) == "short"
        assert clip_line("y" * 50, 10) == "y" * 9 + "…"
        assert clip_line("first\nsecond", 40) == "first"
        assert clip_line("", 40) == ""


class TestNoCodeReachesScrollback:
    def _print(self, ui, text):
        console_ui, buffer = ui
        console_ui.handle(AssistantToken(text=text))
        console_ui.handle(RunFinished(reason="awaiting_approval", stats=SessionStats(token_budget=100)))
        return buffer.getvalue()

    def _assert_collapsed(self, output):
        assert "SECRET" not in output
        assert "▸" in output

    def test_matched_fence(self, ui):
        self._assert_collapsed(self._print(ui, "hi\n\n```python\nSECRET = 1\n```\n\nbye"))

    def test_tilde_fence(self, ui):
        self._assert_collapsed(self._print(ui, "~~~python\nSECRET = 1\n~~~"))

    def test_unterminated_fence(self, ui):
        self._assert_collapsed(self._print(ui, "here:\n\n```python\nSECRET = 1\nmore = 2\n"))

    def test_backtick_fence_closed_by_tildes(self, ui):
        self._assert_collapsed(self._print(ui, "```python\nSECRET = 1\n~~~\n"))

    def test_four_backtick_fence(self, ui):
        self._assert_collapsed(self._print(ui, "````markdown\n```python\nSECRET = 1\n```\n````"))

    def test_indented_block_without_fence(self, ui):
        self._assert_collapsed(self._print(ui, "Example:\n\n    SECRET = 1\n    other = 2\n"))

    def test_fence_inside_list_item(self, ui):
        self._assert_collapsed(self._print(ui, "1. step:\n\n   ```python\n   SECRET = 1\n   ```\n\n2. next"))

    def test_inline_backticks_still_render(self, ui):
        output = self._print(ui, "use `SECRET_NAME` here")
        assert "SECRET_NAME" in output
        assert "▸" not in output

    def test_bold_and_lists_still_render(self, ui):
        output = self._print(ui, "this is **bold** text\n\n- first item\n- second item\n")
        assert "bold" in output
        assert "**" not in output
        assert "first item" in output
        assert "**" not in output
        assert "•" in output


class TestPackageRows:
    def _row(self, ui, **result):
        console_ui, buffer = ui
        console_ui.handle(ToolCallStarted(tool_call_id="1", name="install_package", args={"package_name": "rich"}))
        console_ui.handle(ToolResult(tool_call_id="1", name="install_package", **result))
        return buffer.getvalue()

    def test_successful_install_does_not_print_json(self, ui):
        output = self._row(
            ui, content='{"success": true, "output": "Collecting rich"}', ok=True,
            data={"success": True, "output": "Collecting rich"},
        )
        assert "Collecting rich" in output
        assert '{"success"' not in output

    def test_failed_install_shows_the_pip_error_in_a_panel(self, ui):
        error = "ERROR: No matching distribution found for nopkg\nline two of the pip error"
        output = self._row(
            ui, content="{}", ok=False, data={"success": False, "error": error},
        )
        assert '{"success"' not in output
        assert "line two of the pip error" in output
        assert "install_package" in output


class TestReadRows:
    def test_clipped_read_is_marked_honestly(self, ui):
        console_ui, buffer = ui
        content = "a\nb\n\n\n[...content clipped...]\n\n\nc\nd"
        console_ui.handle(ToolCallStarted(tool_call_id="1", name="read_file_content", args={"path": "big.py"}))
        console_ui.handle(ToolResult(tool_call_id="1", name="read_file_content", content=content, ok=True))
        output = buffer.getvalue()
        assert "~4 lines (clipped)" in output
        assert "content clipped" not in output


class TestContentFallbackIsBounded:
    def test_long_multiline_content_is_one_short_line(self, ui):
        console_ui, buffer = ui
        content = "Error: " + "z" * 200 + "\nSECRET second line"
        console_ui.handle(ToolResult(tool_call_id="unseen", name="read_file_content", content=content, ok=False))
        lines = [line for line in buffer.getvalue().splitlines() if line.strip()]
        assert len(lines) == 1
        assert "SECRET" not in lines[0]
        assert "z" * 40 not in lines[0]
        assert "…" in lines[0]


def shown(console_ui) -> str:
    probe = Console(file=io.StringIO(), width=100, no_color=True)
    probe.print(console_ui.status.render())
    return probe.file.getvalue()


def edit_args(name="a.py", find=""):
    return {"filename": name, "find_str": find, "replace_str": "x = 1"}


def approve(console_ui, requests, monkeypatch, answer=True):
    monkeypatch.setattr("src.ui.console.Confirm.ask", lambda *a, **k: answer)
    return console_ui.ask_approval(requests, Session())


def req(call_id, tool, args):
    return ApprovalRequest(call_id, tool, args, f"{tool} {call_id}")


class TestPhaseAcrossApproval:
    def test_resumed_drive_shows_the_approved_phase_from_the_first_frame(self, ui, monkeypatch):
        console_ui, _ = ui
        console_ui.begin_turn()
        console_ui.handle(ToolCallStarted(tool_call_id="1", name="edit_file", args=edit_args()))
        console_ui.handle(ApprovalRequested(requests=[req("1", "edit_file", edit_args())]))
        console_ui.handle(RunFinished(reason="awaiting_approval", stats=SessionStats(token_budget=100)))
        console_ui.end_turn()

        approve(console_ui, [req("1", "edit_file", edit_args())], monkeypatch)
        console_ui.begin_turn()
        try:
            output = shown(console_ui)
            assert "Drafting" in output
            assert "a.py" in output
            console_ui.handle(ToolResult(tool_call_id="1", name="edit_file", content="Success: File created.", ok=True))
            assert "Thinking" in shown(console_ui)
        finally:
            console_ui.end_turn()

    def test_patch_and_run_phases_survive_the_pause(self, ui, monkeypatch):
        console_ui, _ = ui
        approve(console_ui, [req("1", "edit_file", edit_args(find="x"))], monkeypatch)
        console_ui.begin_turn()
        assert "Patching" in shown(console_ui)
        console_ui.end_turn()
        approve(console_ui, [req("2", "run_sandboxed_code", {"filename": "a.py"})], monkeypatch)
        console_ui.begin_turn()
        try:
            assert "Running" in shown(console_ui)
        finally:
            console_ui.end_turn()

    def test_rejection_sets_no_phase(self, ui, monkeypatch):
        console_ui, _ = ui
        approve(console_ui, [req("1", "edit_file", edit_args())], monkeypatch, answer=False)
        console_ui.begin_turn()
        try:
            output = shown(console_ui)
            assert "Thinking" in output
            assert "a.py" not in output
        finally:
            console_ui.end_turn()

    def test_remembered_call_does_not_leak_past_the_turn(self, ui, monkeypatch):
        console_ui, _ = ui
        approve(console_ui, [req("1", "edit_file", edit_args())], monkeypatch)
        console_ui.begin_turn()
        console_ui.end_turn()
        console_ui.begin_turn()
        try:
            assert "Thinking" in shown(console_ui)
        finally:
            console_ui.end_turn()

    def test_drive_begins_the_turn_through_the_ui(self, ui, monkeypatch):
        console_ui, _ = ui
        approve(console_ui, [req("1", "edit_file", edit_args())], monkeypatch)
        seen = {}

        def events():
            seen["shown"] = shown(console_ui)
            yield RunFinished(reason="completed", stats=SessionStats(token_budget=100))

        main.drive(events(), Session(), console_ui)
        assert "Drafting" in seen["shown"]
        assert console_ui.status.is_active is False

    def test_pending_approval_after_restart_renders_a_patch_row(self, ui, monkeypatch):
        console_ui, buffer = ui
        approve(console_ui, [req("9", "edit_file", edit_args(find="x = 1"))], monkeypatch)
        console_ui.begin_turn()
        console_ui.handle(ToolResult(tool_call_id="9", name="edit_file", content="Success: File edited.", ok=True))
        console_ui.end_turn()
        row = next(line for line in buffer.getvalue().splitlines() if "a.py" in line)
        assert "patch" in row
        assert "create" not in row
        assert console_ui._call_args == {}


class TestBatchedCalls:
    def test_first_pending_call_is_what_shows(self, ui):
        console_ui, _ = ui
        console_ui.begin_turn()
        try:
            console_ui.handle(ToolCallStarted(tool_call_id="1", name="edit_file", args=edit_args("first.py")))
            console_ui.handle(ToolCallStarted(tool_call_id="2", name="run_sandboxed_code", args={"filename": "second.py"}))
            output = shown(console_ui)
            assert "first.py" in output
            assert "second.py" not in output
            assert "Drafting" in output
        finally:
            console_ui.end_turn()

    def test_results_advance_the_head_then_leave(self, ui):
        console_ui, _ = ui
        console_ui.begin_turn()
        try:
            console_ui.handle(ToolCallStarted(tool_call_id="1", name="edit_file", args=edit_args("first.py")))
            console_ui.handle(ToolCallStarted(tool_call_id="2", name="run_sandboxed_code", args={"filename": "second.py"}))
            console_ui.handle(ToolResult(tool_call_id="1", name="edit_file", content="ok", ok=True))
            output = shown(console_ui)
            assert "second.py" in output
            assert "Running" in output
            console_ui.handle(ToolResult(
                tool_call_id="2", name="run_sandboxed_code", content="{}", ok=True,
                data={"stdout": "", "stderr": "", "exit_code": 0},
            ))
            assert "Thinking" in shown(console_ui)
        finally:
            console_ui.end_turn()

    def test_approved_batch_resumes_on_the_first_call(self, ui, monkeypatch):
        console_ui, _ = ui
        approve(console_ui, [
            req("1", "edit_file", edit_args("first.py")),
            req("2", "run_sandboxed_code", {"filename": "second.py"}),
        ], monkeypatch)
        console_ui.begin_turn()
        try:
            assert "first.py" in shown(console_ui)
            console_ui.handle(ToolResult(tool_call_id="1", name="edit_file", content="ok", ok=True))
            assert "second.py" in shown(console_ui)
        finally:
            console_ui.end_turn()


class TestCodeLabels:
    def test_approval_requests_supply_labels_in_order(self, ui):
        console_ui, buffer = ui
        console_ui.handle(AssistantToken(text="```python\na\n```\n\n```python\nb\n```"))
        console_ui.handle(ApprovalRequested(requests=[
            req("1", "edit_file", edit_args("one.py")),
            req("2", "edit_file", edit_args("two.py")),
        ]))
        console_ui.handle(RunFinished(reason="awaiting_approval", stats=SessionStats(token_budget=100)))
        output = buffer.getvalue()
        assert output.index("one.py") < output.index("two.py")

    def test_executed_file_does_not_shift_labels(self, ui):
        console_ui, buffer = ui
        console_ui.handle(CodeGenerated(filename="run.py", language="python", code=None))
        console_ui.handle(ApprovalRequested(requests=[
            req("1", "run_sandboxed_code", {"filename": "run.py"}),
        ]))
        console_ui.handle(AssistantToken(text="```python\na\n```"))
        console_ui.handle(RunFinished(reason="awaiting_approval", stats=SessionStats(token_budget=100)))
        output = buffer.getvalue()
        assert "run.py" not in output
        assert "▸ python (1 line)" in output

    def test_code_generated_with_code_is_a_secondary_source(self, ui):
        console_ui, buffer = ui
        console_ui.handle(CodeGenerated(filename="gen.py", language="python", code="a"))
        console_ui.handle(AssistantToken(text="```python\na\n```"))
        console_ui.handle(RunFinished(reason="completed", stats=SessionStats(token_budget=100)))
        assert "▸ gen.py (1 line)" in buffer.getvalue()


class TestListDirectoryRow:
    def test_reports_entry_count(self, ui):
        console_ui, buffer = ui
        content = "Contents of directory '.':\n- a.py (File)\n- sub (Directory)"
        console_ui.handle(ToolCallStarted(tool_call_id="1", name="list_directory", args={"path": "."}))
        console_ui.handle(ToolResult(tool_call_id="1", name="list_directory", content=content, ok=True))
        output = buffer.getvalue()
        assert "2 entries" in output
        assert "Contents of" not in output

    def test_single_and_empty(self, ui):
        console_ui, buffer = ui
        console_ui.handle(ToolResult(
            tool_call_id="1", name="list_directory", ok=True,
            content="Contents of directory '.':\n- a.py (File)",
        ))
        console_ui.handle(ToolResult(
            tool_call_id="2", name="list_directory", ok=True, content="Directory '.' is empty.",
        ))
        output = buffer.getvalue()
        assert "1 entry" in output
        assert "empty" in output
