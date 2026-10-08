import io
from datetime import datetime, timedelta, timezone

import pytest
from rich.console import Console

import src.sandbox.paths as paths_module
from src.service.events import SessionInfo
from src.ui.commands import handle_command
from src.ui.console import ConsoleUI
from src.ui.sessions import (
    PickerRow,
    SessionPicker,
    _PickerKeys,
    VisibleRows,
    build_rows,
    format_age,
    pick_session,
    to_formatted_text,
    to_rich,
)


@pytest.fixture(autouse=True)
def tmp_workspace_root(tmp_path, monkeypatch):
    monkeypatch.setattr(paths_module, "WORKSPACE_ROOT", tmp_path)
    return tmp_path


@pytest.fixture
def con():
    buffer = io.StringIO()
    console = Console(file=buffer, width=100, force_terminal=False, no_color=True)
    return console, buffer


def answer(monkeypatch, value):
    import rich.prompt

    monkeypatch.setattr(rich.prompt.Prompt, "ask", staticmethod(lambda *a, **k: value))

NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)


def ago(**kwargs):
    return NOW - timedelta(**kwargs)


def infos(count, files=1):
    return [
        SessionInfo(
            session_id=f"s{index:02d}",
            files=[f"f{n}.py" for n in range(files)],
            has_workspace=True,
            updated_at=NOW - timedelta(hours=index),
        )
        for index in range(count)
    ]


class TestFormatAge:
    def test_none_is_unknown(self):
        assert format_age(None, NOW) == "unknown"

    def test_seconds_is_just_now(self):
        assert format_age(ago(seconds=5), NOW) == "just now"

    def test_just_under_a_minute(self):
        assert format_age(ago(seconds=59), NOW) == "just now"

    def test_one_minute(self):
        assert format_age(ago(minutes=1), NOW) == "1m ago"

    def test_fifty_nine_minutes(self):
        assert format_age(ago(minutes=59), NOW) == "59m ago"

    def test_one_hour(self):
        assert format_age(ago(hours=1), NOW) == "1h ago"

    def test_twenty_three_hours(self):
        assert format_age(ago(hours=23), NOW) == "23h ago"

    def test_one_day_is_yesterday(self):
        assert format_age(ago(hours=25), NOW) == "yesterday"

    def test_two_days(self):
        assert format_age(ago(days=2), NOW) == "2d ago"

    def test_a_week(self):
        assert format_age(ago(days=7), NOW) == "7d ago"

    def test_naive_timestamp_is_treated_as_utc(self):
        naive = ago(hours=2).replace(tzinfo=None)
        assert format_age(naive, NOW) == "2h ago"

    def test_future_timestamp_clamps_to_just_now(self):
        assert format_age(NOW + timedelta(hours=1), NOW) == "just now"

    def test_now_defaults_to_the_clock(self):
        assert format_age(datetime.now(timezone.utc)) == "just now"


class TestBuildRows:
    def test_one_row_per_info(self):
        assert len(build_rows(infos(3), now=NOW)) == 3

    def test_row_carries_id_age_and_files(self):
        row = build_rows(infos(2), now=NOW)[1]
        assert row.session_id == "s01"
        assert row.age == "1h ago"
        assert row.files == "1 file"

    def test_plural_and_empty_file_labels(self):
        assert build_rows(infos(1, files=3), now=NOW)[0].files == "3 files"
        assert build_rows(infos(1, files=0), now=NOW)[0].files == "no files"

    def test_current_is_marked(self):
        rows = build_rows(infos(3), current="s01", now=NOW)
        assert [row.is_current for row in rows] == [False, True, False]

    def test_absent_current_marks_nothing(self):
        rows = build_rows(infos(3), current="nope", now=NOW)
        assert not any(row.is_current for row in rows)

    def test_no_cursor_by_default(self):
        assert not any(row.is_cursor for row in build_rows(infos(3), now=NOW))

    def test_cursor_marks_one_row(self):
        rows = build_rows(infos(3), now=NOW, cursor=2)
        assert [row.is_cursor for row in rows] == [False, False, True]


class TestSessionPickerMovement:
    def test_starts_at_the_top(self):
        assert SessionPicker(infos(5), now=NOW).cursor == 0

    def test_moves_down(self):
        picker = SessionPicker(infos(5), now=NOW)
        picker.move(1)
        assert picker.cursor == 1

    def test_wraps_from_last_to_first(self):
        picker = SessionPicker(infos(3), now=NOW)
        picker.move(1)
        picker.move(1)
        picker.move(1)
        assert picker.cursor == 0

    def test_wraps_from_first_to_last(self):
        picker = SessionPicker(infos(3), now=NOW)
        picker.move(-1)
        assert picker.cursor == 2

    def test_single_item_stays_put(self):
        picker = SessionPicker(infos(1), now=NOW)
        picker.move(1)
        picker.move(-1)
        assert picker.cursor == 0

    def test_empty_list_does_not_raise(self):
        picker = SessionPicker([], now=NOW)
        picker.move(1)
        assert picker.cursor == 0
        assert picker.selected is None

    def test_selected_follows_the_cursor(self):
        picker = SessionPicker(infos(3), now=NOW)
        picker.move(2)
        assert picker.selected.session_id == "s02"


class TestSessionPickerWindow:
    def test_short_list_shows_everything(self):
        visible = SessionPicker(infos(4), height=10, now=NOW).visible()
        assert len(visible.rows) == 4
        assert not visible.more_above and not visible.more_below

    def test_long_list_is_windowed(self):
        visible = SessionPicker(infos(20), height=5, now=NOW).visible()
        assert len(visible.rows) == 5
        assert not visible.more_above
        assert visible.more_below

    def test_scrolling_down_moves_the_window(self):
        picker = SessionPicker(infos(20), height=5, now=NOW)
        for _ in range(6):
            picker.move(1)
        visible = picker.visible()
        assert visible.more_above and visible.more_below
        assert [row.session_id for row in visible.rows][0] == "s02"

    def test_cursor_is_always_visible(self):
        picker = SessionPicker(infos(20), height=5, now=NOW)
        for step in range(25):
            picker.move(1)
            assert any(row.is_cursor for row in picker.visible().rows), step

    def test_wrapping_to_the_end_shows_the_last_page(self):
        picker = SessionPicker(infos(20), height=5, now=NOW)
        picker.move(-1)
        visible = picker.visible()
        assert visible.more_above and not visible.more_below
        assert visible.rows[-1].session_id == "s19"
        assert visible.rows[-1].is_cursor


def plain(texts):
    return [text.plain for text in texts]


def visible_of(count, **kwargs):
    return SessionPicker(infos(count), now=NOW, **kwargs).visible()


class TestRichAdapter:
    def test_one_text_per_row(self):
        assert len(to_rich(visible_of(3))) == 3

    def test_row_contains_id_age_and_files(self):
        line = plain(to_rich(visible_of(3)))[1]
        assert "s01" in line and "1h ago" in line and "1 file" in line

    def test_cursor_row_is_marked(self):
        lines = plain(to_rich(visible_of(3)))
        assert lines[0].startswith("❯")
        assert lines[1].startswith(" ")

    def test_legacy_windows_uses_an_ascii_cursor(self):
        con = Console(file=io.StringIO(), width=80, force_terminal=False, no_color=True)
        con.legacy_windows = True
        assert plain(to_rich(visible_of(3), con))[0].startswith(">")

    def test_current_row_is_tagged(self):
        visible = SessionPicker(infos(3), current="s01", now=NOW).visible()
        assert "(current)" in plain(to_rich(visible))[1]

    def test_scroll_markers_are_added(self):
        lines = plain(to_rich(visible_of(20, height=5)))
        assert len(lines) == 6
        assert lines[-1].strip() == "⋮"


def mixed_width_infos():
    return [
        SessionInfo(session_id="9bb6618a-0693-4720-b241-77ef2457a84a", updated_at=NOW),
        SessionInfo(session_id="my-session", updated_at=NOW),
    ]


class TestIdColumnWidth:
    def test_sizes_to_the_longest_id(self):
        visible = SessionPicker(mixed_width_infos(), now=NOW).visible()
        assert visible.id_width == 36

    def test_ages_line_up_across_mixed_length_ids(self):
        visible = SessionPicker(mixed_width_infos(), now=NOW).visible()
        columns = [line.plain.index("just now") for line in to_rich(visible)]
        assert len(set(columns)) == 1

    def test_width_holds_steady_while_scrolling(self):
        long_id = "9bb6618a-0693-4720-b241-77ef2457a84a"
        picker = SessionPicker(
            [SessionInfo(session_id=long_id, updated_at=NOW)]
            + [SessionInfo(session_id=f"s{n}", updated_at=NOW) for n in range(10)],
            height=3,
            now=NOW,
        )
        widths = set()
        for _ in range(12):
            widths.add(picker.visible().id_width)
            picker.move(1)
        assert widths == {36}

    def test_empty_list_has_no_width(self):
        assert SessionPicker([], now=NOW).visible().id_width == 0


class TestFormattedTextAdapter:
    def test_returns_style_text_pairs(self):
        fragments = to_formatted_text(visible_of(3))
        assert all(isinstance(pair, tuple) and len(pair) == 2 for pair in fragments)

    def test_text_contains_every_session_id(self):
        text = "".join(part for _, part in to_formatted_text(visible_of(3)))
        assert "s00" in text and "s01" in text and "s02" in text

    def test_one_newline_per_row(self):
        text = "".join(part for _, part in to_formatted_text(visible_of(3)))
        assert text.count("\n") == 3

    def test_cursor_row_uses_the_cursor_class(self):
        styles = {style for style, _ in to_formatted_text(visible_of(3))}
        assert "class:picker.cursor" in styles

    def test_every_class_is_declared_in_the_style_rules(self):
        from src.ui.sessions import PICKER_STYLE_RULES

        visible = SessionPicker(infos(20), current="s01", height=5, now=NOW).visible()
        for style, _ in to_formatted_text(visible):
            if style.startswith("class:"):
                assert style[len("class:"):] in PICKER_STYLE_RULES


class TestPickSessionFallback:
    def test_empty_list_returns_none_and_says_so(self, con):
        console, buffer = con
        assert pick_session([], console) is None
        assert "No saved sessions yet." in buffer.getvalue()

    def test_lists_every_session_numbered(self, con, monkeypatch):
        console, buffer = con
        answer(monkeypatch, "")
        pick_session(infos(3), console)
        output = buffer.getvalue()
        assert "1." in output and "2." in output and "3." in output
        assert "s00" in output and "s02" in output

    def test_number_selects_that_session(self, con, monkeypatch):
        console, _ = con
        answer(monkeypatch, "2")
        assert pick_session(infos(3), console) == "s01"

    def test_blank_cancels(self, con, monkeypatch):
        console, _ = con
        answer(monkeypatch, "")
        assert pick_session(infos(3), console) is None

    def test_non_numeric_cancels(self, con, monkeypatch):
        console, _ = con
        answer(monkeypatch, "abc")
        assert pick_session(infos(3), console) is None

    def test_out_of_range_cancels(self, con, monkeypatch):
        console, _ = con
        answer(monkeypatch, "99")
        assert pick_session(infos(3), console) is None

    def test_zero_cancels(self, con, monkeypatch):
        console, _ = con
        answer(monkeypatch, "0")
        assert pick_session(infos(3), console) is None

    def test_marks_the_current_session(self, con, monkeypatch):
        console, buffer = con
        answer(monkeypatch, "")
        pick_session(infos(3), console, current="s01")
        assert "(current)" in buffer.getvalue()


class TestPickSessionRouting:
    def test_interactive_terminal_runs_the_application(self, con, monkeypatch):
        console, _ = con
        import src.ui.sessions as sessions_module

        monkeypatch.setattr(sessions_module, "_interactive", lambda _console: True)
        monkeypatch.setattr(sessions_module, "_run_application", lambda picker, console: "chosen")
        assert pick_session(infos(3), console) == "chosen"

    def test_application_failure_falls_back(self, con, monkeypatch):
        console, _ = con
        import src.ui.sessions as sessions_module

        def boom(picker, console):
            raise RuntimeError("no terminal")

        monkeypatch.setattr(sessions_module, "_interactive", lambda _console: True)
        monkeypatch.setattr(sessions_module, "_run_application", boom)
        answer(monkeypatch, "1")
        assert pick_session(infos(3), console) == "s00"

    def test_non_terminal_console_is_not_interactive(self, con):
        from src.ui.sessions import _interactive

        console, _ = con
        assert _interactive(console) is False


class TestConsoleUIDelegates:
    def test_pick_session_delegates(self, con, monkeypatch):
        console, _ = con
        answer(monkeypatch, "1")
        assert ConsoleUI(console=console).pick_session(infos(2)) == "s00"


class TestPickerKeys:
    def test_down_moves_the_cursor(self):
        picker = SessionPicker(infos(3), now=NOW)
        _PickerKeys(picker).down()
        assert picker.cursor == 1

    def test_up_wraps(self):
        picker = SessionPicker(infos(3), now=NOW)
        _PickerKeys(picker).up()
        assert picker.cursor == 2

    def test_accept_records_the_selected_id(self):
        picker = SessionPicker(infos(3), now=NOW)
        keys = _PickerKeys(picker)
        keys.down()
        keys.accept()
        assert keys.result == "s01"
        assert keys.done is True

    def test_accept_on_an_empty_list_records_none(self):
        keys = _PickerKeys(SessionPicker([], now=NOW))
        keys.accept()
        assert keys.result is None
        assert keys.done is True

    def test_cancel_records_none(self):
        picker = SessionPicker(infos(3), now=NOW)
        keys = _PickerKeys(picker)
        keys.down()
        keys.cancel()
        assert keys.result is None
        assert keys.done is True

    def test_starts_undone_with_no_result(self):
        keys = _PickerKeys(SessionPicker(infos(3), now=NOW))
        assert keys.result is None and keys.done is False


class RecordingSession:
    def __init__(self, session_id="current-id"):
        self.session_id = session_id
        self.closed = False

    def close(self):
        self.closed = True


class StubUI:
    def __init__(self, choice):
        self.choice = choice
        self.notes = []

    def pick_session(self, infos, current=None):
        self.seen_current = current
        return self.choice

    def print_note(self, message):
        self.notes.append(message)

    def print_error(self, message):
        self.notes.append(message)


@pytest.fixture
def no_saved_sessions(monkeypatch):
    import src.ui.commands as commands_module

    monkeypatch.setattr(commands_module, "list_sessions", lambda: [])


class TestSessionsCommand:
    def test_cancelling_leaves_the_session_alone(self, no_saved_sessions):
        session = RecordingSession()
        ui = StubUI(choice=None)
        result = handle_command("/sessions", session, ui)
        assert result.session is None
        assert session.closed is False

    def test_choosing_the_current_session_is_a_no_op(self, no_saved_sessions):
        session = RecordingSession()
        ui = StubUI(choice="current-id")
        result = handle_command("/sessions", session, ui)
        assert result.session is None
        assert session.closed is False
        assert any("Already in" in note for note in ui.notes)

    def test_choosing_another_session_swaps_and_closes(self, no_saved_sessions):
        session = RecordingSession()
        ui = StubUI(choice="20261006-9c22")
        result = handle_command("/sessions", session, ui)
        assert session.closed is True
        assert result.session is not None
        assert result.session.session_id == "20261006-9c22"
        assert any("Resumed session" in note for note in ui.notes)

    def test_passes_the_current_id_to_the_picker(self, no_saved_sessions):
        ui = StubUI(choice=None)
        handle_command("/sessions", RecordingSession("abc"), ui)
        assert ui.seen_current == "abc"


class TestPrintSessions:
    def test_shows_the_age_and_the_file_names(self, con):
        console, buffer = con
        recent = SessionInfo(
            session_id="s00",
            files=["f0.py", "f1.py"],
            has_workspace=True,
            updated_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        ConsoleUI(console=console).print_sessions([recent])
        output = buffer.getvalue()
        assert "s00" in output and "1h ago" in output and "f0.py" in output

    def test_unknown_age_when_no_timestamp(self, con):
        console, buffer = con
        ConsoleUI(console=console).print_sessions(
            [SessionInfo(session_id="s99", files=[], has_workspace=False)]
        )
        assert "unknown" in buffer.getvalue()
