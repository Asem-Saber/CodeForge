from src.ui.status import PHASES, STEP_VERBS, TOOL_PHASES, format_tokens, phase_for, step_verb


class TestPhaseFor:
    def test_edit_with_empty_find_str_is_creating(self):
        assert phase_for("edit_file", {"find_str": ""}) == "creating"

    def test_edit_with_missing_find_str_is_creating(self):
        assert phase_for("edit_file", {}) == "creating"

    def test_edit_with_find_str_is_patching(self):
        assert phase_for("edit_file", {"find_str": "x = 1"}) == "patching"

    def test_known_tools_map_to_phases(self):
        assert phase_for("run_sandboxed_code", {}) == "running"
        assert phase_for("install_package", {}) == "installing"
        assert phase_for("read_file_content", {}) == "reading"
        assert phase_for("list_directory", {}) == "listing"
        assert phase_for("validate_python_syntax", {}) == "checking"
        assert phase_for("validate_imports", {}) == "checking"

    def test_unknown_tool_falls_back_to_thinking(self):
        assert phase_for("t", {}) == "thinking"

    def test_every_phase_has_a_word_pool(self):
        for tool in ("edit_file", "run_sandboxed_code", "install_package",
                     "read_file_content", "list_directory", "validate_imports", "t"):
            assert PHASES[phase_for(tool, {})]
        assert PHASES["patching"]
        assert PHASES["retrying"]

    def test_mappings_only_name_known_phases(self):
        assert set(TOOL_PHASES.values()) <= set(PHASES)
        assert set(STEP_VERBS) <= set(PHASES)


class TestStepVerb:
    def test_verbs_are_short(self):
        assert step_verb("edit_file", {"find_str": ""}) == "create"
        assert step_verb("edit_file", {"find_str": "x"}) == "patch"
        assert step_verb("run_sandboxed_code", {}) == "run"
        assert step_verb("install_package", {}) == "install"
        assert step_verb("read_file_content", {}) == "read"
        assert step_verb("list_directory", {}) == "list"
        assert step_verb("validate_imports", {}) == "check"

    def test_unknown_tool_uses_its_own_name(self):
        assert step_verb("t", {}) == "t"


class TestFormatTokens:
    def test_format_tokens(self):
        assert format_tokens(999) == "999"
        assert format_tokens(4200) == "4.2k"


import io

import pytest
from rich.console import Console

from src.service.events import SessionStats
from src.ui.status import StatusRegion, target_of


@pytest.fixture
def region():
    buffer = io.StringIO()
    console = Console(file=buffer, width=100, force_terminal=False, no_color=True)
    clock = [0.0]
    return StatusRegion(console, clock=lambda: clock[0]), clock, buffer


def shown(region_fixture) -> str:
    status, _clock, buffer = region_fixture
    buffer.truncate(0)
    buffer.seek(0)
    status.console.print(status.render())
    return buffer.getvalue()


class TestStatusRegion:
    def test_thinking_word_rotates_with_the_clock(self, region):
        status, clock, _ = region
        status.start()
        assert "Thinking" in shown(region)
        clock[0] = 4.0
        assert "Noodling" in shown(region)
        clock[0] = 8.0
        assert "Pondering" in shown(region)
        clock[0] = 16.0
        assert "Thinking" in shown(region)

    def test_elapsed_seconds_are_shown(self, region):
        status, clock, _ = region
        status.start()
        clock[0] = 12.7
        assert "12s" in shown(region)

    def test_tool_phase_picks_its_own_pool(self, region):
        status, _clock, _ = region
        status.start()
        status.enter_tool("run_sandboxed_code", {})
        assert "Running" in shown(region)

    def test_tool_row_shows_target_not_contents(self, region):
        status, _clock, _ = region
        status.start()
        status.enter_tool("edit_file", {"filename": "a.py", "find_str": "", "replace_str": "x = 1\n" * 200})
        output = shown(region)
        assert "a.py" in output
        assert "x = 1" not in output

    def test_leave_tool_returns_to_thinking(self, region):
        status, _clock, _ = region
        status.start()
        status.enter_tool("install_package", {"package_name": "rich"})
        status.leave_tool()
        output = shown(region)
        assert "Thinking" in output
        assert "rich" not in output

    def test_recent_error_reads_as_retrying(self, region):
        status, _clock, _ = region
        status.start()
        status.update_stats(SessionStats(has_recent_error=True))
        assert "Regrouping" in shown(region)

    def test_active_tool_outranks_retrying(self, region):
        status, _clock, _ = region
        status.start()
        status.update_stats(SessionStats(has_recent_error=True))
        status.enter_tool("run_sandboxed_code", {})
        output = shown(region)
        assert "Running" in output
        assert "Regrouping" not in output

    def test_tokens_and_retry_count(self, region):
        status, _clock, _ = region
        status.start()
        status.update_stats(SessionStats(total_tokens_used=4200, retry_count=2, max_retries=3))
        output = shown(region)
        assert "4.2k tokens" in output
        assert "retry 2/3" in output

    def test_no_stats_means_no_token_counter(self, region):
        status, _clock, _ = region
        status.start()
        assert "tokens" not in shown(region)

    def test_interrupt_hint_is_always_present(self, region):
        status, _clock, _ = region
        status.start()
        assert "ctrl+c" in shown(region)

    def test_start_and_stop_are_idempotent(self, region):
        status, _clock, _ = region
        assert status.is_active is False
        status.start()
        status.start()
        assert status.is_active is True
        status.stop()
        status.stop()
        assert status.is_active is False
        status.pause()
        assert status.is_active is False

    def test_second_start_does_not_reset_elapsed(self, region):
        status, clock, _ = region
        status.start()
        clock[0] = 9.0
        status.start()
        assert "9s" in shown(region)

    def test_nothing_is_written_to_a_non_terminal_console(self, region):
        status, _clock, buffer = region
        status.start()
        status.enter_tool("run_sandboxed_code", {})
        status.stop()
        assert buffer.getvalue() == ""

    def test_pause_keeps_stats_but_stop_clears_them(self, region):
        status, _clock, _ = region
        status.start()
        status.update_stats(SessionStats(total_tokens_used=4200))
        status.pause()
        status.start()
        assert "4.2k tokens" in shown(region)
        status.stop()
        status.start()
        assert "tokens" not in shown(region)

    def test_phase_and_row_change_together(self, region):
        status, _clock, _ = region
        status.start()
        status.enter_tool("run_sandboxed_code", {"filename": "t.py"})
        status.enter_tool("edit_file", {"filename": "a.py", "find_str": ""})
        output = shown(region)
        assert "Drafting" in output
        assert "t.py" not in output

    def test_pause_tears_down_an_active_region(self, region):
        status, _clock, _ = region
        status.start()
        status.pause()
        assert status.is_active is False

    def test_live_rerenders_on_every_refresh(self):
        console = Console(file=io.StringIO(), force_terminal=True, width=100, no_color=True)
        clock = [0.0]
        status = StatusRegion(console, clock=lambda: clock[0])
        probe = Console(file=io.StringIO(), width=100, no_color=True)

        def frame() -> str:
            probe.file.truncate(0)
            probe.file.seek(0)
            probe.print(status._live.get_renderable())
            return probe.file.getvalue()

        status.start()
        try:
            first = frame()
            clock[0] = 7.0
            second = frame()
        finally:
            status.stop()
        assert "0s" in first
        assert "7s" in second
        assert first != second

    def test_second_turn_starts_clean(self, region):
        status, clock, _ = region
        status.start()
        status.enter_tool("run_sandboxed_code", {})
        status.update_stats(SessionStats(has_recent_error=True, retry_count=2, max_retries=3))
        clock[0] = 30.0
        status.stop()
        clock[0] = 100.0
        status.start()
        clock[0] = 102.0
        output = shown(region)
        assert "Thinking" in output
        assert "Regrouping" not in output
        assert "Running" not in output
        assert "run " not in output
        assert "tokens" not in output
        assert "retry" not in output
        assert "2s" in output
        assert "30s" not in output


class TestTargetOf:
    def test_prefers_filename_over_path_and_package(self):
        assert target_of({"package_name": "p", "path": "b", "filename": "a.py"}) == "a.py"

    def test_prefers_path_over_package(self):
        assert target_of({"package_name": "p", "path": "b"}) == "b"

    def test_package_name_is_used(self):
        assert target_of({"package_name": "rich"}) == "rich"

    def test_short_values_fall_back_to_key_value(self):
        assert target_of({"a": 1, "b": "x"}) == "a=1, b=x"

    def test_long_values_are_summarised_by_length(self):
        assert target_of({"code": "x" * 41}) == "code=<41 chars>"
        assert target_of({"code": "x" * 40}) == "code=<40 chars>"

    def test_newlines_are_flattened(self):
        assert target_of({"cmd": "a\nb"}) == "cmd=a b"

    def test_empty_args_give_a_dash(self):
        assert target_of({}) == "—"

    def test_code_only_args_render_a_dash(self):
        assert target_of({"code": "x=1\nprint(x)"}) == "code=<12 chars>"

    def test_short_code_only_is_summarized_not_verbatim(self):
        assert target_of({"code": "SECRET = 1"}) == "code=<10 chars>"
        assert target_of({"code": "x = 1"}) == "code=<5 chars>"

    def test_code_with_other_args_still_key_value(self):
        assert target_of({"code": "x=1\ny=2", "n": 1}) == "code=x=1 y=2, n=1"
