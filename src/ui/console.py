import difflib
import re

from rich.console import Console, Group
from rich.markdown import CodeBlock, Markdown
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
from src.ui.banner import render_banner
from src.ui.sessions import format_age, pick_session
from src.ui.status import StatusRegion, format_tokens, step_verb, target_of

THEME = Theme({
    "cf.brand": "bold orange3",
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


def last_line(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-1].strip() if lines else ""


def clip_line(text: str, width: int) -> str:
    lines = text.strip().splitlines()
    if not lines:
        return ""
    line = lines[0].strip()
    return line if len(line) <= width else line[: width - 1] + "…"


def result_glyph(console, ok: bool) -> str:
    if getattr(console, "legacy_windows", False):
        return "+" if ok else "!"
    return "✓" if ok else "✗"


CLIP_SENTINEL = "[...content clipped...]"


class CollapsedCode(CodeBlock):
    """Renders a code block as a one-line reference instead of the code."""

    def __rich_console__(self, console, options):
        count = len(self.text.plain.rstrip("\n").splitlines())
        yield Text(
            f"▸ {self.lexer_name or 'code'} ({count} line{'s' if count != 1 else ''})",
            style="cf.status",
        )


class SafeMarkdown(Markdown):
    """Markdown that cannot render a code block, whatever its fence form."""

    elements = {**Markdown.elements, "fence": CollapsedCode, "code_block": CollapsedCode}


FENCE = re.compile(
    r"^([ \t]*)(```|~~~)([^\n]*)\n(.*?)^[ \t]*\2[ \t]*$",
    re.DOTALL | re.MULTILINE,
)


def strip_code_fences(text: str, filenames: list[str] | None = None) -> str:
    text = text.replace("\r\n", "\n")
    names = list(filenames or [])

    def replace(match):
        label = names.pop(0) if names else (match.group(3).strip() or "code")
        count = len(match.group(4).splitlines())
        return f"{match.group(1)}▸ {label} ({count} line{'s' if count != 1 else ''})"

    return FENCE.sub(replace, text)


class ConsoleUI:
    """Renders the core event stream to a terminal."""

    def __init__(self, console: Console | None = None):
        self.console = console or Console()
        self.console.push_theme(THEME)
        self.status = StatusRegion(self.console)
        self._buffer = ""
        self._call_args: dict[str, dict] = {}
        self._call_names: dict[str, str] = {}
        self._outstanding: list[str] = []
        self._approved: list[str] = []
        self._code_files: list[str] = []

    def begin_turn(self):
        self.status.start()
        self._outstanding = list(self._approved)
        self._approved = []
        self._show_head()

    def _show_head(self):
        if not self._outstanding:
            self.status.leave_tool()
            return
        head = self._outstanding[0]
        self.status.enter_tool(self._call_names.get(head, ""), self._call_args.get(head, {}))


    def handle(self, event, session=None):
        if isinstance(event, AssistantToken):
            self._buffer += event.text
            return

        if session is not None and isinstance(event, (ToolCallStarted, ToolResult)):
            self.status.update_stats(session.stats())

        if isinstance(event, AssistantMessage):
            if event.content.strip():
                self._buffer += ("\n\n" if self._buffer else "") + event.content

        elif isinstance(event, ToolCallStarted):
            self._call_args[event.tool_call_id] = event.args
            self._call_names[event.tool_call_id] = event.name
            idle = not self._outstanding
            self._outstanding.append(event.tool_call_id)
            if idle:
                self._show_head()

        elif isinstance(event, ToolResult):
            if event.tool_call_id in self._outstanding:
                self._outstanding.remove(event.tool_call_id)
            self._show_head()
            self._render_result(event)

        elif isinstance(event, CodeGenerated):
            if event.filename and event.code is not None:
                self._code_files.append(event.filename)

        elif isinstance(event, ApprovalRequested):
            for request in event.requests:
                filename = request.args.get("filename")
                if filename and (request.tool != "run_sandboxed_code" or request.args.get("code")):
                    self._code_files.append(filename)

        elif isinstance(event, RunFinished):
            self.status.stop()
            self._flush_prose()
            if event.reason == "completed":
                self.console.print(self._status_line(event.stats))

    def end_turn(self):
        """Tear down after a turn, whether it finished or died mid-stream."""
        self.status.stop()
        self._buffer = ""
        self._code_files.clear()
        self._outstanding.clear()
        self._approved.clear()

    def _flush_prose(self):
        text = strip_code_fences(self._buffer, self._code_files)
        self._buffer = ""
        self._code_files.clear()
        if text.strip():
            self.console.print(SafeMarkdown(text))

    def _render_result(self, event: ToolResult):
        args = self._call_args.pop(event.tool_call_id, {})
        self._call_names.pop(event.tool_call_id, None)
        style = "cf.ok" if event.ok else "cf.fail"

        row = Text.assemble(
            (result_glyph(self.console, event.ok), style),
            (f" {step_verb(event.name, args):<8} ", "cf.tool"),
            (target_of(args), "cf.status"),
        )
        metric = self._metric(event, args)
        if metric:
            row.append(f"  {metric}", style=style)
        self.console.print(row)

        data = event.data or {}
        if not event.ok and any(key in data for key in ("stdout", "stderr", "error", "output")):
            parts = (data.get("stdout"), data.get("stderr"), data.get("error"), data.get("output"))
            body = "\n".join(str(p) for p in parts if p).strip()
            suffix = f" - exit {data['exit_code']}" if "exit_code" in data else ""
            self.console.print(Panel(
                Text(clip(body, MAX_OUTPUT_LINES) or "(no output)"),
                title=f"{event.name}{suffix}",
                border_style=style,
                title_align="left",
            ))

    def _metric(self, event: ToolResult, args: dict) -> str:
        data = event.data or {}

        if "exit_code" in data:
            exit_part = f"exit {data['exit_code']}"
            tail = last_line(data.get("stdout") or "")
            return f"{exit_part} · {clip_line(tail, 40)}" if tail else exit_part

        for key in ("error", "output"):
            if data.get(key):
                return clip_line(str(data[key]), 40)

        if not event.ok:
            return clip_line(event.content, 40)

        if event.name == "edit_file" and args:
            added = len((args.get("replace_str") or "").splitlines())
            removed = len((args.get("find_str") or "").splitlines())
            return f"+{added}/-{removed}" if removed else f"+{added}"

        if event.name == "list_directory":
            entries = sum(1 for line in event.content.splitlines() if line.startswith("- "))
            if entries:
                return f"{entries} entr{'y' if entries == 1 else 'ies'}"
            return "empty" if "is empty" in event.content else clip_line(event.content, 40)

        if event.name == "read_file_content":
            if CLIP_SENTINEL in event.content:
                head, _, tail = event.content.partition(CLIP_SENTINEL)
                shown = head.rstrip().splitlines() + tail.strip().splitlines()
                return f"~{len(shown)} lines (clipped)"
            count = len(event.content.splitlines())
            return f"{count} line{'s' if count != 1 else ''}"

        return clip_line(event.content, 40)

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

    # Approval

    def ask_approval(self, requests, session) -> bool:
        self.status.pause()
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
        approved = Confirm.ask("Approve?", console=self.console, default=False)
        for request in requests:
            self._call_args[request.tool_call_id] = request.args
            self._call_names[request.tool_call_id] = request.tool
        if approved:
            self._approved = [request.tool_call_id for request in requests]
        return approved

    # Standalone output

    def banner(self, session, resumed: bool):
        self.console.print(render_banner(session, resumed, self.console))

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
                (f"  {format_age(info.updated_at):<11}", "cf.status"),
                (summary, "cf.status"),
            ))

    def pick_session(self, infos, current=None) -> str | None:
        return pick_session(infos, self.console, current)

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
