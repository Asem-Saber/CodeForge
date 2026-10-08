import sys
from dataclasses import dataclass
from datetime import datetime, timezone

from rich.prompt import Prompt
from rich.text import Text

from src.config import logger

MINUTE = 60
HOUR = 60 * MINUTE
DAY = 24 * HOUR


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def format_age(updated_at: datetime | None, now: datetime | None = None) -> str:
    if updated_at is None:
        return "unknown"

    now = _as_utc(now) if now is not None else datetime.now(timezone.utc)
    seconds = max(0, int((now - _as_utc(updated_at)).total_seconds()))

    if seconds < MINUTE:
        return "just now"
    if seconds < HOUR:
        return f"{seconds // MINUTE}m ago"
    if seconds < DAY:
        return f"{seconds // HOUR}h ago"
    if seconds < 2 * DAY:
        return "yesterday"
    return f"{seconds // DAY}d ago"


@dataclass(frozen=True)
class PickerRow:
    session_id: str
    age: str
    files: str
    is_current: bool = False
    is_cursor: bool = False


@dataclass(frozen=True)
class VisibleRows:
    rows: tuple[PickerRow, ...]
    more_above: bool = False
    more_below: bool = False
    id_width: int = 0


def files_label(count: int) -> str:
    if count == 0:
        return "no files"
    return "1 file" if count == 1 else f"{count} files"


def id_width_of(rows) -> int:
    return max((len(row.session_id) for row in rows), default=0)


def build_rows(infos, current=None, now=None, cursor=None) -> tuple[PickerRow, ...]:
    return tuple(
        PickerRow(
            session_id=info.session_id,
            age=format_age(info.updated_at, now),
            files=files_label(len(info.files)),
            is_current=info.session_id == current,
            is_cursor=index == cursor,
        )
        for index, info in enumerate(infos)
    )


class SessionPicker:
    def __init__(self, infos, current=None, height: int = 10, now=None):
        self.infos = list(infos)
        self.current = current
        self.height = max(1, height)
        self.now = now
        self.cursor = 0
        self._top = 0

    def move(self, delta: int):
        if not self.infos:
            return
        self.cursor = (self.cursor + delta) % len(self.infos)
        self._scroll()

    def _scroll(self):
        if self.cursor < self._top:
            self._top = self.cursor
        elif self.cursor >= self._top + self.height:
            self._top = self.cursor - self.height + 1
        self._top = max(0, min(self._top, max(0, len(self.infos) - self.height)))

    @property
    def selected(self):
        return self.infos[self.cursor] if self.infos else None

    def visible(self) -> VisibleRows:
        rows = build_rows(self.infos, self.current, self.now, cursor=self.cursor)
        window = rows[self._top : self._top + self.height]
        return VisibleRows(
            rows=window,
            more_above=self._top > 0,
            more_below=self._top + self.height < len(rows),
            id_width=id_width_of(rows),
        )


AGE_WIDTH = 11

PICKER_STYLE_RULES = {
    "picker.title": "bold",
    "picker.cursor": "ansiyellow",
    "picker.row": "",
    "picker.dim": "ansibrightblack",
    "picker.current": "ansiyellow",
}


def _cursor_glyph(console) -> str:
    return ">" if getattr(console, "legacy_windows", False) else "❯"


def _scroll_glyph(console) -> str:
    return "." if getattr(console, "legacy_windows", False) else "⋮"


def to_rich(visible: VisibleRows, console=None) -> list[Text]:
    cursor = _cursor_glyph(console)
    scroll = Text(f"  {_scroll_glyph(console)}", style="cf.status")

    lines = [scroll] if visible.more_above else []
    for row in visible.rows:
        line = Text.assemble(
            (f"{cursor} " if row.is_cursor else "  ", "cf.warn"),
            (f"{row.session_id:<{visible.id_width}}  ", "cf.session"),
            (f"{row.age:<{AGE_WIDTH}}", "cf.status"),
            (row.files, "cf.status"),
        )
        if row.is_current:
            line.append("   (current)", style="cf.warn")
        lines.append(line)
    if visible.more_below:
        lines.append(scroll)
    return lines


def to_formatted_text(visible: VisibleRows) -> list[tuple[str, str]]:
    fragments: list[tuple[str, str]] = []
    if visible.more_above:
        fragments.append(("class:picker.dim", "  ⋮\n"))
    for row in visible.rows:
        style = "class:picker.cursor" if row.is_cursor else "class:picker.row"
        fragments.append(
            (style, f"{'❯ ' if row.is_cursor else '  '}{row.session_id:<{visible.id_width}}  ")
        )
        fragments.append(("class:picker.dim", f"{row.age:<{AGE_WIDTH}}{row.files}"))
        if row.is_current:
            fragments.append(("class:picker.current", "   (current)"))
        fragments.append(("", "\n"))
    if visible.more_below:
        fragments.append(("class:picker.dim", "  ⋮\n"))
    return fragments


MAX_WINDOW = 10
MIN_WINDOW = 3
CHROME_LINES = 6


def _window_height(console) -> int:
    height = getattr(getattr(console, "size", None), "height", 24) or 24
    return min(MAX_WINDOW, max(MIN_WINDOW, height - CHROME_LINES))


def _interactive(console) -> bool:
    if not getattr(console, "is_terminal", False):
        return False
    try:
        return bool(sys.stdin.isatty())
    except Exception:
        return False


class _PickerKeys:
    def __init__(self, picker: "SessionPicker"):
        self.picker = picker
        self.result: str | None = None
        self.done = False

    def up(self):
        self.picker.move(-1)

    def down(self):
        self.picker.move(1)

    def accept(self):
        selected = self.picker.selected
        self.result = selected.session_id if selected else None
        self.done = True

    def cancel(self):
        self.result = None
        self.done = True


HEADER_STYLE = "class:picker.title"
FOOTER_TEXT = "  ↑↓ move · enter resume · esc cancel"


def _run_application(picker, console) -> str | None:
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import HSplit, Layout, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.styles import Style

    actions = _PickerKeys(picker)
    keys = KeyBindings()

    def finish(event):
        event.app.exit(result=actions.result)

    @keys.add("up")
    @keys.add("k")
    def _(event):
        actions.up()

    @keys.add("down")
    @keys.add("j")
    def _(event):
        actions.down()

    @keys.add("enter")
    def _(event):
        actions.accept()
        finish(event)

    @keys.add("escape")
    @keys.add("q")
    @keys.add("c-c")
    def _(event):
        actions.cancel()
        finish(event)

    def body_height() -> int:
        visible = picker.visible()
        return len(visible.rows) + int(visible.more_above) + int(visible.more_below)

    header = Window(
        FormattedTextControl(
            lambda: [(HEADER_STYLE, f"  Resume a session    {len(picker.infos)} saved")]
        ),
        height=1,
    )
    body = Window(FormattedTextControl(lambda: to_formatted_text(picker.visible())), height=body_height)
    footer = Window(FormattedTextControl(lambda: [("class:picker.dim", FOOTER_TEXT)]), height=1)

    app = Application(
        layout=Layout(HSplit([header, Window(height=1), body, Window(height=1), footer])),
        key_bindings=keys,
        style=Style.from_dict(PICKER_STYLE_RULES),
        full_screen=False,
        erase_when_done=True,
        mouse_support=False,
    )
    return app.run()


def _pick_numbered(infos, console, current, now) -> str | None:
    console.print(Text("Resume a session", style="bold"))
    built = build_rows(infos, current, now)
    rows = VisibleRows(rows=built, id_width=id_width_of(built))
    for index, line in enumerate(to_rich(rows, console), start=1):
        numbered = Text(f"{index:>3}. ", style="cf.status")
        numbered.append_text(line)
        console.print(numbered)

    reply = Prompt.ask("Resume which? (blank to cancel)", console=console, default="")
    try:
        choice = int(reply.strip())
    except (AttributeError, TypeError, ValueError):
        return None
    if not 1 <= choice <= len(infos):
        return None
    return infos[choice - 1].session_id


def pick_session(infos, console, current=None, now=None) -> str | None:
    infos = list(infos)
    if not infos:
        console.print(Text("No saved sessions yet.", style="cf.status"))
        return None

    if _interactive(console):
        picker = SessionPicker(infos, current=current, height=_window_height(console), now=now)
        try:
            return _run_application(picker, console)
        except Exception:
            logger.debug("Session picker unavailable; falling back to a prompt", exc_info=True)

    return _pick_numbered(infos, console, current, now)
