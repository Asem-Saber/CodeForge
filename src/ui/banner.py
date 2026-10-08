"""The startup welcome panel."""
from urllib.parse import urlparse

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src import __version__
from src.config import ENDPOINT, MODEL_ID

TWO_COLUMN_WIDTH = 96
STACKED_WIDTH = 72

TIPS = (
    "/help lists commands",
    "/sessions to switch",
    "ctrl+c interrupts, ctrl+d exits",
)

WORDMARK = (
    "█████ █████ ████  █████ █████ █████ ████  █████ █████",
    "█     █   █ █   █ █     █     █   █ █   █ █     █    ",
    "█     █   █ █   █ ████  ████  █   █ ████  █  ██ ████ ",
    "█     █   █ █   █ █     █     █   █ █  █  █   █ █    ",
    "█████ █████ ████  █████ █     █████ █   █ █████ █████",
)

WORDMARK_ASCII = tuple(row.replace("█", "#") for row in WORDMARK)


def wordmark_for(console) -> tuple[str, ...]:
    """The block art, or an ASCII copy on a terminal that cannot draw it."""
    if getattr(console, "legacy_windows", False):
        return WORDMARK_ASCII
    return WORDMARK


def endpoint_host() -> str:
    """The endpoint's host and port — never its path, and never its userinfo.

    `netloc` would carry a `user:password@` prefix straight onto the screen,
    so the host and port are reassembled from the parsed parts instead.
    """
    parsed = urlparse(ENDPOINT)
    if not parsed.hostname:
        return ENDPOINT
    return f"{parsed.hostname}:{parsed.port}" if parsed.port else parsed.hostname


def files_label(count: int) -> str:
    if count == 0:
        return "no files"
    return "1 file" if count == 1 else f"{count} files"


def _left_lines(console) -> list[Text]:
    lines = [Text(row, style="cf.brand") for row in wordmark_for(console)]
    lines.append(Text(""))
    lines.append(Text(f"{MODEL_ID} · {endpoint_host()}", style="cf.status"))
    return lines


def _right_lines(session, resumed: bool) -> list[Text]:
    count = len(session.files())
    return [
        Text("Tips", style="bold"),
        *(Text(tip, style="cf.status") for tip in TIPS),
        Text(""),
        Text("Resuming" if resumed else "New session", style="bold"),
        Text.assemble(
            (session.session_id, "cf.session"),
            (f" · {files_label(count)}", "cf.status"),
        ),
        Text(f"workspace/{session.session_id}", style="cf.status"),
    ]


def _pad(lines: list[Text], height: int) -> list[Text]:
    return lines + [Text("") for _ in range(height - len(lines))]


def render_banner(session, resumed: bool, console) -> Panel:
    """The startup panel, laid out for the width the console reports."""
    right = _right_lines(session, resumed)

    if console.width >= STACKED_WIDTH:
        left = _left_lines(console)
    else:
        left = [
            Text("CodeForge", style="cf.brand"),
            Text(f"{MODEL_ID} · {endpoint_host()}", style="cf.status"),
        ]

    if console.width >= TWO_COLUMN_WIDTH:
        height = max(len(left), len(right))
        grid = Table.grid(padding=(0, 2))
        grid.add_column()
        grid.add_column()
        grid.add_column()
        grid.add_row(
            Group(*_pad(left, height)),
            Group(*[Text("│", style="cf.status") for _ in range(height)]),
            Group(*_pad(right, height)),
        )
        body = grid
    else:
        body = Group(*left, Text(""), *right)

    return Panel(
        body,
        title=f"CodeForge v{__version__}",
        title_align="left",
        border_style="cf.brand",
        padding=(1, 2),
    )
