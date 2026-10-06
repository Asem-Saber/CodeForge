# Status-first console renderer implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace CodeForge's echo-everything transcript with a Claude Code–style one: a single live status line while a turn runs, one collapsed line per finished step, and code on screen only in the approval diff.

**Architecture:** A new `src/ui/status.py` owns the bottom zone — one `Live(transient=True)` holding a spinner, a phase word, elapsed seconds and token usage. `src/ui/console.py` keeps owning scrollback, but stops streaming prose (it buffers to `RunFinished`) and collapses tool results to one row. Rich draws `console.print` output above an active `Live`, which is what makes the two zones work without an alternate screen. No changes to `src/service/events.py`.

**Tech Stack:** Python 3, Rich (`Live`, `Group`, `Text`, `Markdown`, `Panel`, `Syntax`), pytest.

**Spec:** `docs/superpowers/specs/2026-10-06-status-first-console-design.md`

## Global constraints

- Print to normal scrollback only. No alternate screen, no `Console(screen=True)`. Terminal history, copy/paste and piping must keep working.
- Do not modify `src/service/events.py`. Every value the renderer needs is already derivable from the existing events plus `session.stats()`.
- Never call `session.stats()` on an `AssistantToken`. It triggers a LangGraph `get_state()` — a checkpointer read per token. Stats refresh only on `ToolCallStarted` and `ToolResult`.
- Styles come from the existing `THEME` in `console.py`: `cf.tool`, `cf.ok`, `cf.fail`, `cf.warn`, `cf.status`, `cf.session`. Do not add raw colour strings.
- Code content never reaches scrollback. `_format_args` already guards this; keep it as the only path from args to screen.
- `tests/test_ui.py`'s `ui` fixture (`force_terminal=False`, `no_color=True`, `width=100`) stays as it is.
- Run the whole file after every task: `pytest tests/test_ui.py -q`. Final gate: `pytest tests/test_ui.py tests/test_nodes.py tests/test_session.py -q`.

---

## File structure

| File | Responsibility |
|---|---|
| `src/ui/status.py` (create) | The live bottom zone. Phase/verb vocabulary, `format_tokens`, and `StatusRegion` (the only owner of a `Live`). Writes nothing to scrollback. |
| `src/ui/console.py` (modify) | Scrollback rendering only: step rows, failure panels, prose flushing, approval diffs, standalone output. Imports from `status.py`. |
| `main.py` (modify) | `drive()` starts and stops the status region around the event loop. |
| `tests/test_status.py` (create) | `StatusRegion` and the phase/verb functions. |
| `tests/test_ui.py` (modify) | Four existing tests updated; new tests for metrics and prose. |

`format_tokens` moves to `status.py` because `StatusRegion` needs it and `console.py` importing from `status.py` (not the reverse) is what keeps the dependency acyclic. `console.py` re-exports it, so the existing test import keeps working.

---

### Task 1: Phase and verb vocabulary

**Files:**
- Create: `src/ui/status.py`
- Modify: `src/ui/console.py:77-79` (remove `format_tokens`, import it instead)
- Test: `tests/test_status.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `phase_for(tool: str, args: dict) -> str`, `step_verb(tool: str, args: dict) -> str`, `format_tokens(count: int) -> str`, and the module constants `PHASES: dict[str, tuple[str, ...]]`, `TOOL_PHASES: dict[str, str]`, `STEP_VERBS: dict[str, str]`, `SPINNER: str`, `WORD_SECONDS: int`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_status.py`:

```python
from src.ui.status import PHASES, format_tokens, phase_for, step_verb


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_status.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'src.ui.status'`

- [ ] **Step 3: Write minimal implementation**

Create `src/ui/status.py`:

```python
"""The live region at the bottom of the terminal while a turn runs.

Owns the only Rich Live in the renderer. Finished work is printed above it by
ConsoleUI; nothing in here writes to scrollback.
"""

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_status.py -q`
Expected: PASS, 9 passed

- [ ] **Step 5: Move `format_tokens` out of `console.py`**

In `src/ui/console.py`, delete this function (currently at lines 77-79):

```python
def format_tokens(count: int) -> str:
    return f"{count / 1000:.1f}k" if count >= 1000 else str(count)
```

and add to the import block, after the `src.service.events` import:

```python
from src.ui.status import format_tokens, step_verb
```

The re-export keeps `from src.ui.console import format_tokens` working in `tests/test_ui.py:19`. `step_verb` is unused until Task 4; import it now so the import block is touched once.

- [ ] **Step 6: Run the existing suite to verify nothing regressed**

Run: `pytest tests/test_ui.py tests/test_status.py -q`
Expected: PASS, no failures. `TestHelpers::test_format_tokens` in `test_ui.py` still passes via the re-export.

- [ ] **Step 7: Commit**

```bash
git add src/ui/status.py src/ui/console.py tests/test_status.py
git commit -m "add phase and verb vocabulary for the status region"
```

---

### Task 2: `StatusRegion`

**Files:**
- Modify: `src/ui/status.py`
- Test: `tests/test_status.py`

**Interfaces:**
- Consumes: `phase_for`, `PHASES`, `SPINNER`, `WORD_SECONDS`, `format_tokens` from Task 1.
- Produces: `StatusRegion(console, clock=time.monotonic, word_seconds=WORD_SECONDS)` with methods `start()`, `stop()`, `pause()`, `enter_tool(tool: str, args: dict)`, `leave_tool()`, `update_stats(stats: SessionStats)`, and `render() -> Group`. `start()` and `stop()` are idempotent.

Design notes the implementer needs:

- The word is `pool[int(elapsed // word_seconds) % len(pool)]` — a pure function of the clock, so injecting the clock is enough to make tests deterministic. Do not add randomness.
- `retrying` overrides `thinking` only. While a tool is active its own phase wins, even if `stats.has_recent_error` is set.
- `pause()` is `stop()`. It exists as a separate name because `ask_approval` calls it for a different reason (a blocking `Confirm.ask` cannot share a line with a `Live`), and a reader of `ask_approval` should not have to wonder whether stopping is correct there.
- Rich's `Live(transient=True)` on a non-terminal console renders nothing, not even on `stop()`. That is why `render()` is public: it is the only way to test this class.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_status.py`:

```python
import io

import pytest
from rich.console import Console

from src.service.events import SessionStats
from src.ui.status import StatusRegion


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
        status.start()
        status.start()
        status.stop()
        status.stop()
        status.pause()

    def test_nothing_is_written_to_a_non_terminal_console(self, region):
        status, _clock, buffer = region
        status.start()
        status.enter_tool("run_sandboxed_code", {})
        status.stop()
        assert buffer.getvalue() == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_status.py -q`
Expected: collection error, `ImportError: cannot import name 'StatusRegion' from 'src.ui.status'`

- [ ] **Step 3: Write minimal implementation**

Add to the top of `src/ui/status.py`:

```python
import time

from rich.console import Group
from rich.live import Live
from rich.text import Text
```

Append to `src/ui/status.py`:

```python
class StatusRegion:
    """The spinner, phase word, elapsed time and token count.

    The clock is injectable so the rotating word is deterministic under test:
    it is pool[elapsed // word_seconds], with no randomness.
    """

    def __init__(self, console, clock=time.monotonic, word_seconds: int = WORD_SECONDS):
        self.console = console
        self._clock = clock
        self._word_seconds = word_seconds
        self._live: Live | None = None
        self._started = 0.0
        self._phase = "thinking"
        self._step: Text | None = None
        self._stats = None

    # Lifecycle

    def start(self):
        if self._live is not None:
            return
        self._started = self._clock()
        self._phase = "thinking"
        self._step = None
        self._live = Live(
            self.render(),
            console=self.console,
            transient=True,
            refresh_per_second=10,
        )
        self._live.start()

    def stop(self):
        if self._live is None:
            return
        self._live.stop()
        self._live = None
        self._step = None
        self._phase = "thinking"

    def pause(self):
        """Stop so a blocking prompt can own the line. Same teardown as stop()."""
        self.stop()

    # State

    def enter_tool(self, tool: str, args: dict):
        self._phase = phase_for(tool, args)
        self._step = Text.assemble(
            (f"{step_verb(tool, args):<8} ", "cf.tool"),
            (target_of(args), "cf.status"),
        )
        self._refresh()

    def leave_tool(self):
        self._phase = "thinking"
        self._step = None
        self._refresh()

    def update_stats(self, stats):
        self._stats = stats
        self._refresh()

    # Rendering

    def render(self) -> Group:
        rows = []
        if self._step is not None:
            row = Text(f"{self._frame()} ", style="cf.warn")
            row.append_text(self._step)
            rows.append(row)
        rows.append(self._status_text())
        return Group(*rows)

    def _refresh(self):
        if self._live is not None:
            self._live.update(self.render())

    def _elapsed(self) -> int:
        return int(self._clock() - self._started)

    def _frame(self) -> str:
        if getattr(self.console, "legacy_windows", False):
            return "*"
        return SPINNER[int((self._clock() - self._started) * 10) % len(SPINNER)]

    def _word(self) -> str:
        phase = self._phase
        if phase == "thinking" and getattr(self._stats, "has_recent_error", False):
            phase = "retrying"
        pool = PHASES[phase]
        return pool[int(self._elapsed() // self._word_seconds) % len(pool)]

    def _status_text(self) -> Text:
        line = Text.assemble(
            (self._frame(), "cf.warn"),
            (f" {self._word()}… ", ""),
            (f"{self._elapsed()}s", "cf.status"),
        )
        if self._stats is not None:
            line.append(
                f" · {format_tokens(self._stats.total_tokens_used)} tokens", style="cf.status"
            )
            if self._stats.retry_count:
                line.append(
                    f" · retry {self._stats.retry_count}/{self._stats.max_retries}",
                    style="cf.warn",
                )
        line.append(" · ctrl+c to interrupt", style="cf.status")
        return line
```

- [ ] **Step 4: Add `target_of`, shared by the status row and the step row**

`enter_tool` above calls `target_of`. It is the same "name the call without dumping contents" rule as `ConsoleUI._format_args` (`console.py:177`), and Task 4 needs it too, so it lives here rather than being duplicated. Append to `src/ui/status.py`:

```python
def target_of(args: dict) -> str:
    """Identify a call without dumping file contents into the transcript."""
    if not args:
        return "—"

    for key in ("filename", "path", "package_name"):
        if args.get(key):
            return str(args[key])

    parts = []
    for key, value in args.items():
        text = str(value).replace("\n", " ")
        parts.append(f"{key}={text}" if len(text) <= 40 else f"{key}=<{len(text)} chars>")
    return ", ".join(parts) or "—"
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_status.py -q`
Expected: PASS, 21 passed

If `test_nothing_is_written_to_a_non_terminal_console` fails, Rich's `Live` is
emitting something on stop for this console type after all. Do not make the
region non-transient to satisfy it — relax the assertion to
`assert SPINNER[0] not in buffer.getvalue()` and carry on. What matters for the
rest of the plan is that the spinner never lands in scrollback, not that Rich
writes literally zero bytes.

- [ ] **Step 6: Commit**

```bash
git add src/ui/status.py tests/test_status.py
git commit -m "add StatusRegion, the live bottom zone of the transcript"
```

---

### Task 3: Buffer prose and collapse fenced code

**Files:**
- Create: nothing
- Modify: `src/ui/console.py` — add `strip_code_fences`, change `_stream`/`_end_stream`, change the `AssistantToken`/`AssistantMessage`/`CodeGenerated`/`RunFinished` branches of `handle`
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: nothing from Tasks 1-2.
- Produces: `strip_code_fences(text: str, filenames: list[str] | None = None) -> str`. `ConsoleUI` gains `_code_files: list[str]` and `_flush_prose()`.

Behaviour:

- Tokens and node-authored messages both accumulate in `self._buffer`.
- The buffer flushes on `RunFinished` for **both** reasons. `awaiting_approval` matters: it is the message explaining the edit the user is about to approve.
- Each fenced block becomes `▸ fibonacci.py (24 lines)`. The filename is taken from `_code_files` in arrival order — those come from `CodeGenerated`, which `handle` currently discards. When the list runs dry, fall back to the fence's language tag, then to `code`.
- An unterminated fence is left verbatim. The regex requires a closing fence, so this is automatic; the test pins it so nobody "fixes" it into swallowing text.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ui.py`. Extend the existing import on line 19 to include `strip_code_fences`:

```python
from src.ui.console import (
    ConsoleUI,
    clip,
    format_tokens,
    language_for,
    strip_code_fences,
    unified_diff,
)
```

Add a new class after `TestHelpers`:

```python
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
        assert "▸ first.py (1 lines)" in result
        assert "▸ second.py (2 lines)" in result

    def test_falls_back_to_language_then_code(self):
        assert "▸ python (1 lines)" in strip_code_fences("```python\na\n```")
        assert "▸ code (1 lines)" in strip_code_fences("```\na\n```")

    def test_inline_backticks_survive(self):
        assert strip_code_fences("use `x = 1` here") == "use `x = 1` here"

    def test_unterminated_fence_is_left_alone(self):
        text = "```python\nx = 1\n"
        assert strip_code_fences(text) == text


class TestProse:
    def test_tokens_do_not_print_before_the_run_finishes(self, ui):
        console_ui, buffer = ui
        console_ui.handle(AssistantToken(text="hello"))
        assert buffer.getvalue() == ""

    def test_prose_flushes_on_awaiting_approval(self, ui):
        console_ui, buffer = ui
        console_ui.handle(AssistantToken(text="about to edit a.py"))
        console_ui.handle(RunFinished(reason="awaiting_approval", stats=SessionStats(token_budget=100)))
        assert "about to edit a.py" in buffer.getvalue()

    def test_code_in_prose_is_collapsed(self, ui):
        console_ui, buffer = ui
        console_ui.handle(CodeGenerated(filename="fib.py", language="python"))
        console_ui.handle(AssistantToken(text="done:\n\n```python\nx = 1\n```\n"))
        console_ui.handle(RunFinished(reason="completed", stats=SessionStats(token_budget=100)))
        output = buffer.getvalue()
        assert "x = 1" not in output
        assert "fib.py" in output

    def test_buffer_does_not_leak_into_the_next_turn(self, ui):
        console_ui, buffer = ui
        console_ui.handle(AssistantToken(text="first turn"))
        console_ui.handle(RunFinished(reason="completed", stats=SessionStats(token_budget=100)))
        buffer.truncate(0)
        buffer.seek(0)
        console_ui.handle(RunFinished(reason="completed", stats=SessionStats(token_budget=100)))
        assert "first turn" not in buffer.getvalue()
```

- [ ] **Step 2: Update `test_assistant_message`, which breaks by design**

`test_assistant_message` (line 72) asserts on prose without firing `RunFinished`, so buffered prose never flushes. Replace it with:

```python
    def test_assistant_message(self, ui):
        console_ui, buffer = ui
        console_ui.handle(AssistantMessage(content="all done"))
        console_ui.handle(RunFinished(reason="completed", stats=SessionStats(token_budget=100)))
        assert "all done" in buffer.getvalue()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_ui.py -q`
Expected: collection error, `ImportError: cannot import name 'strip_code_fences' from 'src.ui.console'`

- [ ] **Step 4: Write the implementation**

Add `import re` to the top of `src/ui/console.py` beside `import difflib`, and add after `clip` (around line 75):

```python
FENCE = re.compile(r"^```([^\n]*)\n(.*?)^```[ \t]*$", re.DOTALL | re.MULTILINE)


def strip_code_fences(text: str, filenames: list[str] | None = None) -> str:
    """Replace each fenced block with a one-line reference to what it wrote.

    Filenames are consumed in arrival order from the turn's CodeGenerated
    events; the fence's language tag is the fallback. An unterminated fence has
    no closing match and is left verbatim.
    """
    names = list(filenames or [])

    def replace(match):
        label = names.pop(0) if names else (match.group(1).strip() or "code")
        return f"▸ {label} ({len(match.group(2).splitlines())} lines)"

    return FENCE.sub(replace, text)
```

In `ConsoleUI.__init__`, add alongside `self._call_args`:

```python
        self._code_files: list[str] = []
```

Replace the `AssistantToken` branch and the `_stream`/`_end_stream` methods. The `handle` branches become:

```python
        if isinstance(event, AssistantToken):
            self._buffer += event.text
            return

        if isinstance(event, AssistantMessage):
            if event.content.strip():
                self._buffer += ("\n\n" if self._buffer else "") + event.content
```

```python
        elif isinstance(event, CodeGenerated):
            if event.filename:
                self._code_files.append(event.filename)
```

```python
        elif isinstance(event, RunFinished):
            self._flush_prose()
            if event.reason == "completed":
                self.console.print(self._status_line(event.stats))
```

Delete `_stream` and `_end_stream` entirely, along with the `self._live` attribute in `__init__` and the `_end_stream()` call at the top of `ask_approval`. Add:

```python
    def _flush_prose(self):
        text = strip_code_fences(self._buffer, self._code_files)
        self._buffer = ""
        self._code_files.clear()
        if text.strip():
            self.console.print(Markdown(text))
```

Remove the now-unused `Live` import from the `rich.live` line.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_ui.py -q`
Expected: PASS for `TestStripCodeFences`, `TestProse`, `test_assistant_message`, and `test_assistant_tokens_are_flushed` (which already fires `RunFinished`, so it needed no change). `test_tool_call`, `test_tool_call_shows_filename_not_contents` and `test_long_args_are_summarized` still pass at this point — `ToolCallStarted` is not touched until Task 5.

- [ ] **Step 6: Commit**

```bash
git add src/ui/console.py tests/test_ui.py
git commit -m "buffer prose until the run finishes and collapse fenced code"
```

---

### Task 4: Collapse tool results to one row

**Files:**
- Modify: `src/ui/console.py` — replace `_render_result` (lines 152-168), replace `_format_args` (line 177) with a delegation to `target_of`
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: `step_verb` and `target_of` from Tasks 1-2.
- Produces: `result_glyph(console, ok: bool) -> str`, `last_line(text: str) -> str`, `clip_line(text: str, width: int) -> str`, and `ConsoleUI._metric(event: ToolResult, args: dict) -> str`.

Row layout: `glyph`, `verb` padded to 8, `target`, `metric`. The metric rules, in the order the implementation must test them:

| Condition | Metric |
|---|---|
| `data` has `exit_code`, stdout non-empty | `exit {code} · {last non-empty stdout line}` clipped to 40 |
| `data` has `exit_code`, stdout empty | `exit {code}` |
| `edit_file` **and** stashed args exist, no `find_str` | `+{lines in replace_str}` |
| `edit_file` **and** stashed args exist, with `find_str` | `+{lines in replace_str}/-{lines in find_str}` |
| `read_file_content` | `{n} lines` |
| anything else | first line of `event.content`, clipped to 40 |

The last row is not just a courtesy for unknown tools: a `ToolResult` can arrive with no `ToolCallStarted` the renderer ever saw (resumed session, or a unit test), and the tool's own return string is then the only honest thing left to show. This is why the `edit_file` rules are gated on `args` being non-empty.

Failures keep the full `Panel` of stdout/stderr. Successes do not.

- [ ] **Step 1: Write the failing test**

Extend the `src.ui.console` import in `tests/test_ui.py` to add `clip_line` and `last_line`, then add a new class:

```python
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
        assert len([line for line in output.splitlines() if line.strip()]) == 1

    def test_run_with_no_stdout_shows_only_exit(self, ui):
        console_ui, buffer = ui
        output = self._row(
            console_ui, buffer, "run_sandboxed_code", {"filename": "t.py"},
            content="{}", ok=True,
            data={"stdout": "", "stderr": "", "exit_code": 0},
        )
        assert "exit 0" in output

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


class TestLineHelpers:
    def test_last_line_skips_blanks(self):
        assert last_line("a\n\nb\n\n") == "b"
        assert last_line("   \n") == ""

    def test_clip_line_truncates(self):
        assert clip_line("short", 40) == "short"
        assert clip_line("y" * 50, 10) == "y" * 9 + "…"
        assert clip_line("first\nsecond", 40) == "first"
        assert clip_line("", 40) == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ui.py -q`
Expected: collection error, `ImportError: cannot import name 'clip_line' from 'src.ui.console'`

- [ ] **Step 3: Write the helpers**

Add to `src/ui/console.py` after `clip`:

```python
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
```

- [ ] **Step 4: Replace `_render_result`**

Replace the whole of `_render_result` in `src/ui/console.py` with:

```python
    def _render_result(self, event: ToolResult):
        args = self._call_args.pop(event.tool_call_id, {})
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
        if not event.ok and ("stdout" in data or "stderr" in data):
            body = "\n".join(p for p in (data.get("stdout", ""), data.get("stderr", "")) if p).strip()
            self.console.print(Panel(
                Text(clip(body, MAX_OUTPUT_LINES) or "(no output)"),
                title=f"{event.name} - exit {data.get('exit_code', '?')}",
                border_style=style,
                title_align="left",
            ))

    def _metric(self, event: ToolResult, args: dict) -> str:
        data = event.data or {}

        if "exit_code" in data:
            exit_part = f"exit {data['exit_code']}"
            tail = last_line(data.get("stdout", ""))
            return f"{exit_part} · {clip_line(tail, 40)}" if tail else exit_part

        if event.name == "edit_file" and args:
            added = len(args.get("replace_str", "").splitlines())
            removed = len(args.get("find_str", "").splitlines())
            return f"+{added}/-{removed}" if removed else f"+{added}"

        if event.name == "read_file_content":
            return f"{len(event.content.splitlines())} lines"

        return clip_line(event.content, 40)
```

Add `target_of` to the `src.ui.status` import line added in Task 1:

```python
from src.ui.status import format_tokens, step_verb, target_of
```

- [ ] **Step 5: Delete `_format_args` and point its callers at `target_of`**

`_format_args` (line 177) is now duplicated by `target_of` in `status.py`. Delete the method, and change its one remaining caller — the `ToolCallStarted` branch of `handle` — from `self._format_args(event.args)` to `target_of(event.args)`. Task 5 deletes that branch's print entirely, but leaving a call to a deleted method in the tree between tasks would break every test in the file.

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_ui.py -q`
Expected: `TestStepRows` and `TestLineHelpers` pass. `test_simple_result_line`, `test_execution_result_shows_output_and_exit_code` and `test_failed_result_is_shown` also still pass unchanged — verify this rather than assuming it; they are the regression net for the fallback and panel rules.

- [ ] **Step 7: Commit**

```bash
git add src/ui/console.py tests/test_ui.py
git commit -m "collapse tool results to a single step row"
```

---

### Task 5: Wire the status region into the turn

**Files:**
- Modify: `src/ui/console.py` — `__init__`, the `ToolCallStarted`/`ToolResult`/`RunFinished` branches of `handle`, `ask_approval`
- Modify: `main.py:52-54` (`drive`)
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: `StatusRegion` from Task 2; the step rows from Task 4.
- Produces: `ConsoleUI.status: StatusRegion`, public so `main.drive` can bracket the event loop with it.

The stats-refresh placement is the constraint that matters: `session.stats()` calls LangGraph's `get_state()`, which reads the checkpointer. Calling it per `AssistantToken` would do that once per streamed token. The `AssistantToken` branch returns before any stats call, and only `ToolCallStarted`/`ToolResult` refresh.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ui.py`:

```python
class TestStatusWiring:
    def test_tool_call_no_longer_writes_to_scrollback(self, ui):
        console_ui, buffer = ui
        console_ui.handle(ToolCallStarted(tool_call_id="1", name="edit_file", args={"filename": "a.py"}))
        assert buffer.getvalue() == ""

    def test_run_finished_stops_the_region(self, ui):
        console_ui, _ = ui
        console_ui.status.start()
        console_ui.handle(RunFinished(reason="completed", stats=SessionStats(token_budget=100)))
        assert console_ui.status._live is None

    def test_approval_pauses_the_region(self, ui, monkeypatch):
        console_ui, _ = ui
        monkeypatch.setattr("src.ui.console.Confirm.ask", lambda *a, **k: True)
        console_ui.status.start()
        console_ui.ask_approval([], Session())
        assert console_ui.status._live is None

    def test_tokens_never_trigger_a_stats_read(self, ui):
        console_ui, _ = ui

        class ExplodingSession:
            def stats(self):
                raise AssertionError("stats() must not be called for a token")

        console_ui.handle(AssistantToken(text="hello"), ExplodingSession())
```

- [ ] **Step 2: Update the three tests that break by design**

`ToolCallStarted` stops writing to scrollback, so these three — which fire only that event — now see an empty buffer. Rewrite them to fire the matching `ToolResult` and assert on the step row, preserving each one's original intent.

Replace `test_tool_call` (line 77):

```python
    def test_tool_call(self, ui):
        console_ui, buffer = ui
        args = {"filename": "a.py", "find_str": "", "replace_str": "x = 1"}
        console_ui.handle(ToolCallStarted(tool_call_id="1", name="edit_file", args=args))
        console_ui.handle(ToolResult(tool_call_id="1", name="edit_file", content="Success: File created.", ok=True))
        output = buffer.getvalue()
        assert "create" in output
        assert "a.py" in output
```

Replace `test_tool_call_shows_filename_not_contents` (line 110):

```python
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
```

Replace `test_long_args_are_summarized` (line 121):

```python
    def test_long_args_are_summarized(self, ui):
        console_ui, buffer = ui
        console_ui.handle(ToolCallStarted(tool_call_id="1", name="validate_imports", args={"code": "y" * 300}))
        console_ui.handle(ToolResult(tool_call_id="1", name="validate_imports", content="ok", ok=True))
        output = buffer.getvalue()
        assert "chars" in output
        assert "yyyy" not in output
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_ui.py -q`
Expected: FAIL. `test_tool_call_no_longer_writes_to_scrollback` fails because `handle` still prints the `> edit_file` line; `test_run_finished_stops_the_region` fails with `AttributeError: 'ConsoleUI' object has no attribute 'status'`.

- [ ] **Step 4: Wire it into `ConsoleUI`**

In `src/ui/console.py`, extend the `src.ui.status` import:

```python
from src.ui.status import StatusRegion, format_tokens, step_verb, target_of
```

In `__init__`, after `push_theme`:

```python
        self.status = StatusRegion(self.console)
```

Replace the `ToolCallStarted` branch — the whole `self.console.print(Text.assemble(...))` block — with:

```python
        elif isinstance(event, ToolCallStarted):
            self._call_args[event.tool_call_id] = event.args
            self.status.enter_tool(event.name, event.args)
```

Add the stats refresh and the `leave_tool` call. The top of `handle` becomes:

```python
    def handle(self, event, session=None):
        if isinstance(event, AssistantToken):
            self._buffer += event.text
            return

        if session is not None and isinstance(event, (ToolCallStarted, ToolResult)):
            self.status.update_stats(session.stats())
```

and the `ToolResult` branch:

```python
        elif isinstance(event, ToolResult):
            self.status.leave_tool()
            self._render_result(event)
```

and the `RunFinished` branch gains the stop as its first line:

```python
        elif isinstance(event, RunFinished):
            self.status.stop()
            self._flush_prose()
            if event.reason == "completed":
                self.console.print(self._status_line(event.stats))
```

In `ask_approval`, the first line becomes:

```python
        self.status.pause()
```

- [ ] **Step 5: Bracket the event loop in `main.py`**

Replace `drive` (`main.py:52-54`):

```python
def drive(events, session: Session, ui: ConsoleUI):
    ui.status.start()
    try:
        for event in events:
            ui.handle(event, session)
    finally:
        ui.status.stop()
```

The `finally` is what keeps a `KeyboardInterrupt` or an exception mid-turn from leaving the terminal with a hidden cursor and a stale spinner.

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_ui.py tests/test_status.py -q`
Expected: PASS, no failures.

- [ ] **Step 7: Run the full non-Docker suite**

Run: `pytest tests/test_ui.py tests/test_status.py tests/test_nodes.py tests/test_session.py tests/test_validation.py tests/test_paths.py tests/test_file_ops.py -q`
Expected: PASS. `tests/test_e2e.py` and `tests/test_sandbox_integration.py` need Docker and are out of scope for this change — they do not import the renderer.

- [ ] **Step 8: Drive it by hand**

Run: `python main.py`, then ask for something that writes and runs a file, e.g. `write fib.py with a memoized fibonacci and run it`.

Check, in order:
1. A spinner with a rotating word appears immediately after Enter, before any output.
2. The word tracks the real phase — `Drafting` while writing, `Running` during the sandbox call.
3. No code appears in the transcript. The approval prompt shows the diff.
4. Finished steps are one line each and stay in scrollback above the spinner.
5. The spinner is gone after the turn, leaving the `turn n/m · tokens` line.
6. `ctrl+c` mid-turn leaves a usable terminal with a visible cursor.

- [ ] **Step 9: Commit**

```bash
git add src/ui/console.py main.py tests/test_ui.py
git commit -m "render a live status region instead of echoing model output"
```

---

## Out of scope

Both are listed as non-goals in the spec and need machinery this plan does not build:

- `ctrl+o` to expand a collapsed step — needs retained payloads per step.
- `esc` to interrupt — needs a non-blocking key reader; `ctrl+c` already works.
