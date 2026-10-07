import time

from rich.console import Group
from rich.live import Live
from rich.text import Text

THINKING = ("Thinking", "Noodling", "Pondering", "Mulling")

PHASES = {
    "thinking": THINKING,
    "creating": ("Drafting", "Writing", "Composing"),
    "patching": ("Patching", "Splicing", "Revising"),
    "running": ("Running", "Executing"),
    "installing": ("Fetching", "Installing"),
    "reading": ("Reading", "Skimming"),
    "listing": ("Looking around", "Browsing"),
    "checking": ("Checking", "Vetting"),
    "retrying": ("Regrouping", "Rethinking"),
}

TOOL_PHASES = {
    "run_sandboxed_code": "running",
    "install_package": "installing",
    "read_file_content": "reading",
    "list_directory": "listing",
    "validate_python_syntax": "checking",
    "validate_imports": "checking",
}

STEP_VERBS = {
    "creating": "create",
    "patching": "patch",
    "running": "run",
    "installing": "install",
    "reading": "read",
    "listing": "list",
    "checking": "check",
}

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
WORD_SECONDS = 4


def phase_for(tool: str, args: dict) -> str:
    """Which phase a tool call represents. Unknown tools read as thinking."""
    if tool == "edit_file":
        return "patching" if args.get("find_str") else "creating"
    return TOOL_PHASES.get(tool, "thinking")


def step_verb(tool: str, args: dict) -> str:
    """The short left-hand label on a finished step row."""
    return STEP_VERBS.get(phase_for(tool, args), tool)


def format_tokens(count: int) -> str:
    return f"{count / 1000:.1f}k" if count >= 1000 else str(count)


def target_of(args: dict) -> str:
    """Identify a call without dumping file contents into the transcript."""
    if not args:
        return "—"

    for key in ("filename", "path", "package_name"):
        if args.get(key):
            return str(args[key])

    code = args.get("code")
    if set(args) == {"code"} and isinstance(code, str):
        return f"code=<{len(code)} chars>"

    parts = []
    for key, value in args.items():
        text = str(value).replace("\n", " ")
        parts.append(f"{key}={text}" if len(text) <= 40 else f"{key}=<{len(text)} chars>")
    return ", ".join(parts) or "—"


class StatusRegion:
    def __init__(self, console, clock=time.monotonic, word_seconds: int = WORD_SECONDS):
        self.console = console
        self._clock = clock
        self._word_seconds = word_seconds
        self._live: Live | None = None
        self._started = 0.0
        self._activity: tuple[str, Text | None] = ("thinking", None)
        self._stats = None

    def start(self):
        if self._live is not None:
            return
        self._started = self._clock()
        self._activity = ("thinking", None)
        self._live = Live(
            get_renderable=self.render,
            console=self.console,
            transient=True,
            refresh_per_second=10,
        )
        self._live.start()

    def stop(self):
        """Turn boundary: tear down and forget the turn's stats."""
        self._teardown()
        self._stats = None

    def pause(self):
        """Within-turn suspension so a prompt can own the line; keeps the stats."""
        self._teardown()

    def _teardown(self):
        if self._live is None:
            return
        self._live.stop()
        self._live = None
        self._activity = ("thinking", None)

    @property
    def is_active(self) -> bool:
        return self._live is not None

    def enter_tool(self, tool: str, args: dict):
        phase = phase_for(tool, args)
        step = Text.assemble(
            (f"{step_verb(tool, args):<8} ", "cf.tool"),
            (target_of(args), "cf.status"),
        )
        self._activity = (phase, step)
        self._refresh()

    def leave_tool(self):
        self._activity = ("thinking", None)
        self._refresh()

    def update_stats(self, stats):
        self._stats = stats
        self._refresh()

    def render(self) -> Group:
        rows = []
        activity = self._activity
        step = activity[1]
        if step is not None:
            row = Text(f"{self._frame()} ", style="cf.warn")
            row.append_text(step)
            rows.append(row)
        rows.append(self._status_text(activity))
        return Group(*rows)

    def _refresh(self):
        if self._live is not None:
            self._live.refresh()

    def _elapsed(self) -> int:
        return int(self._clock() - self._started)

    def _frame(self) -> str:
        if getattr(self.console, "legacy_windows", False):
            return "*"
        return SPINNER[int((self._clock() - self._started) * 10) % len(SPINNER)]

    def _word(self, phase: str, stats) -> str:
        if phase == "thinking" and getattr(stats, "has_recent_error", False):
            phase = "retrying"
        pool = PHASES[phase]
        return pool[int(self._elapsed() // self._word_seconds) % len(pool)]

    def _status_text(self, activity: tuple[str, Text | None]) -> Text:
        stats = self._stats
        phase = activity[0]
        line = Text.assemble(
            (self._frame(), "cf.warn"),
            (f" {self._word(phase, stats)}… ", ""),
            (f"{self._elapsed()}s", "cf.status"),
        )
        if stats is not None:
            line.append(
                f" · {format_tokens(stats.total_tokens_used)} tokens", style="cf.status"
            )
            if stats.retry_count:
                line.append(
                    f" · retry {stats.retry_count}/{stats.max_retries}",
                    style="cf.warn",
                )
        line.append(" · ctrl+c to interrupt", style="cf.status")
        return line
