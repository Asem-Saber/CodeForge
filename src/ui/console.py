"""Rich transcript renderer.

Prints to normal scrollback rather than the alternate screen, so terminal
history, copy/paste and piping keep working.
"""
import difflib

from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm
from rich.syntax import Syntax
from rich.text import Text
from rich.theme import Theme

from src.service.events import (
    ApprovalRequested,
    AssistantMessage,
    AssistantToken,
    CodeGenerated,
    RunFinished,
    ToolCallStarted,
    ToolResult,
)

THEME = Theme({
    "cf.tool": "cyan",
    "cf.ok": "green",
    "cf.fail": "red",
    "cf.warn": "yellow",
    "cf.status": "dim",
    "cf.session": "magenta",
})

LANGUAGES = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".json": "json",
    ".sh": "bash",
    ".md": "markdown",
    ".html": "html",
    ".css": "css",
    ".sql": "sql",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".toml": "toml",
}

MAX_OUTPUT_LINES = 40
MAX_PREVIEW_LINES = 60


def language_for(filename: str | None) -> str:
    if not filename:
        return "text"
    for suffix, language in LANGUAGES.items():
        if filename.endswith(suffix):
            return language
    return "text"


def unified_diff(preview) -> str:
    return "\n".join(difflib.unified_diff(
        preview.before.splitlines(),
        preview.after.splitlines(),
        fromfile=f"a/{preview.filename}",
        tofile=f"b/{preview.filename}",
        lineterm="",
    ))


def clip(text: str, max_lines: int) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    hidden = len(lines) - max_lines
    return "\n".join(lines[:max_lines] + [f"... {hidden} more line(s)"])


def format_tokens(count: int) -> str:
    return f"{count / 1000:.1f}k" if count >= 1000 else str(count)


class ConsoleUI:
    """Renders the core event stream to a terminal."""

    def __init__(self, console: Console | None = None):
        self.console = console or Console()
        # Pushed rather than passed to the constructor so a caller-supplied
        # console still resolves the cf.* styles.
        self.console.push_theme(THEME)
        self._live: Live | None = None
        self._buffer = ""
        self._call_args: dict[str, dict] = {}

    # Event rendering

    def handle(self, event, session=None):
        if isinstance(event, AssistantToken):
            self._stream(event.text)
            return

        self._end_stream()

        if isinstance(event, AssistantMessage):
            if event.content.strip():
                self.console.print(Markdown(event.content))

        elif isinstance(event, ToolCallStarted):
            self._call_args[event.tool_call_id] = event.args
            self.console.print(
                Text.assemble(
                    ("> ", "cf.tool"),
                    (event.name, "cf.tool bold"),
                    (f"  {self._format_args(event.args)}", "cf.status"),
                )
            )

        elif isinstance(event, ToolResult):
            self._render_result(event)

        elif isinstance(event, CodeGenerated):
            pass  # Tracked for /files; the tool result already reported it.

        elif isinstance(event, ApprovalRequested):
            pass  # ask_approval renders these; the caller drives the prompt.

        elif isinstance(event, RunFinished):
            if event.reason == "completed":
                self.console.print(self._status_line(event.stats))

    def _stream(self, text: str):
        self._buffer += text
        if self._live is None:
            self._live = Live(console=self.console, refresh_per_second=12)
            self._live.start()
        self._live.update(Markdown(self._buffer))

    def _end_stream(self):
        if self._live is not None:
            if self._buffer.strip():
                self._live.update(Markdown(self._buffer))
            self._live.stop()
            self._live = None
        self._buffer = ""

    def _render_result(self, event: ToolResult):
        style = "cf.ok" if event.ok else "cf.fail"
        data = event.data or {}

        if "stdout" in data or "stderr" in data:
            body = "\n".join(p for p in (data.get("stdout", ""), data.get("stderr", "")) if p).strip()
            exit_code = data.get("exit_code", "?")
            self.console.print(Panel(
                Text(clip(body, MAX_OUTPUT_LINES) or "(no output)"),
                title=f"{event.name} - exit {exit_code}",
                border_style=style,
                title_align="left",
            ))
            return

        lines = event.content.strip().splitlines()
        first = (lines[0] if lines else "")[:200]
        self.console.print(Text.assemble(("  ", style), (first, style)))

    def _status_line(self, stats) -> Text:
        used_ratio = stats.total_tokens_used / stats.token_budget if stats.token_budget else 0
        token_style = "cf.fail" if used_ratio > 0.8 else "cf.status"
        line = Text.assemble(
            (f"turn {stats.turn_count}/{stats.max_turns}", "cf.status"),
            ("  ", "cf.status"),
            (
                f"{format_tokens(stats.total_tokens_used)}/{format_tokens(stats.token_budget)} tokens",
                token_style,
            ),
        )
        if stats.retry_count:
            line.append(f"  retry {stats.retry_count}/{stats.max_retries}", style="cf.warn")
        return line

    def _format_args(self, args: dict) -> str:
        """Identify the call without dumping file contents into the transcript."""
        for key in ("filename", "path", "package_name"):
            if args.get(key):
                return str(args[key])

        parts = []
        for key, value in args.items():
            text = str(value).replace("\n", " ")
            parts.append(f"{key}={text}" if len(text) <= 40 else f"{key}=<{len(text)} chars>")
        return ", ".join(parts)

    # Approval

    def ask_approval(self, requests, session) -> bool:
        self._end_stream()
        blocks = []

        for request in requests:
            blocks.append(Text(request.description.splitlines()[0], style="bold"))
            preview = session.preview(request) if session else None

            if preview is None:
                if request.tool == "run_sandboxed_code":
                    blocks.append(Text("  runs in the Docker sandbox", style="cf.status"))
            elif not preview.applies:
                blocks.append(Text(
                    f"  find_str does not match {preview.filename} - this edit will fail",
                    style="cf.warn",
                ))
            elif preview.is_new:
                blocks.append(Syntax(
                    clip(preview.after, MAX_PREVIEW_LINES),
                    language_for(preview.filename),
                    theme="ansi_dark",
                    line_numbers=False,
                ))
            else:
                blocks.append(Syntax(
                    clip(unified_diff(preview), MAX_PREVIEW_LINES),
                    "diff",
                    theme="ansi_dark",
                ))

        title = "Approval required" if len(requests) == 1 else f"Approval required ({len(requests)} actions)"
        self.console.print(Panel(Group(*blocks), title=title, border_style="cf.warn", title_align="left"))
        return Confirm.ask("Approve?", console=self.console, default=False)

    # Standalone output

    def banner(self, session, resumed: bool):
        label = "Resuming session" if resumed else "New session"
        self.console.print(Text.assemble(
            (f"{label} ", "cf.status"),
            (session.session_id, "cf.session"),
        ))
        files = session.files()
        if files:
            self.console.print(Text(f"{len(files)} file(s) in workspace", style="cf.status"))
        self.console.print(Text("/help for commands", style="cf.status"))

    def print_sessions(self, infos):
        if not infos:
            self.console.print(Text("No saved sessions yet.", style="cf.status"))
            return
        for info in infos:
            summary = ", ".join(info.files[:4]) if info.files else "no files"
            if len(info.files) > 4:
                summary += f", +{len(info.files) - 4} more"
            self.console.print(Text.assemble(
                (info.session_id, "cf.session"),
                (f"  {summary}", "cf.status"),
            ))

    def print_files(self, session):
        files = session.files()
        if not files:
            self.console.print(Text("Workspace is empty.", style="cf.status"))
            return
        for path in files:
            self.console.print(Text(f"  {path}"))

    def print_cost(self, session):
        self.console.print(self._status_line(session.stats()))

    def print_help(self, commands):
        for name, description in commands:
            self.console.print(Text.assemble((f"  {name:<12}", "cf.tool"), (description, "cf.status")))

    def print_error(self, message: str):
        self.console.print(Text(message, style="cf.fail"))

    def print_note(self, message: str):
        self.console.print(Text(message, style="cf.status"))
