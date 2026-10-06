# Status-first console renderer

Date: 2026-10-06
Scope: `src/ui/console.py`, `main.py`, `tests/test_ui.py`

## Problem

`ConsoleUI` echoes everything the model produces. `AssistantToken` feeds a
`Live(Markdown(buffer))`, so every fenced code block is painted into the
transcript as it streams, and a successful `run_sandboxed_code` prints a
40-line `Panel` of stdout. Collapsing is the exception.

The result is a transcript dominated by code the user is about to see again in
the approval diff, and no indication of what the agent is doing between events.

This design inverts the default: collapse by default, expand on failure. While a
turn is in flight the terminal shows one moving line — a spinner, a phase verb,
elapsed time and token usage. Code reaches the screen only in the approval diff.

## Non-goals

- Expanding a collapsed step after the fact (`ctrl+o`). Needs retained payloads.
- Cancelling a turn with `esc`. Needs a non-blocking key reader; `ctrl+c`
  already works through the handler in `main.py`.
- A full-screen TUI. `console.py` deliberately prints to normal scrollback so
  terminal history, copy/paste and piping keep working. That stays true.
- Any change to `src/service/events.py`. The existing event stream is
  sufficient; see "Why no event changes are needed".

## Architecture

### Two zones, one file

A new `_StatusRegion` class in `console.py` owns the bottom of the screen:

- A single `Live(transient=True, refresh_per_second=10)`, started once per turn
  and stopped at `RunFinished`. `transient=True` means the status line erases
  itself on stop rather than freezing into the transcript.
- Finished steps are written with `console.print` *while that Live is running*.
  Rich draws them above the live region, so completed work scrolls normally and
  the status line stays pinned to the bottom.

`ConsoleUI` holds one `_StatusRegion` and replaces `self._live` / `self._buffer`
stream handling with it.

### Lifecycle

| Hook | Call |
|---|---|
| before iterating a turn's events | `status.start()` |
| `ToolCallStarted` | `status.enter_tool(name, args)` |
| `ToolResult` | `status.leave_tool()` |
| `ApprovalRequested`, `ask_approval` | `status.pause()` |
| `RunFinished` | `status.stop()` |

`start()` is called from `drive()` in `main.py` rather than from an event, so
the spinner appears the moment the user presses Enter — before the first token
arrives. `drive()` wraps its loop in `try/finally` so an exception or
`KeyboardInterrupt` mid-turn still stops the Live region and restores the
cursor.

`pause()` must stop the Live region before `Confirm.ask` runs, or the prompt and
the spinner fight over the same line. It replaces the `_end_stream()` call at the
top of `ask_approval`.

### Phase model

`_StatusRegion` tracks `phase`, `started_at`, and the active tool. The displayed
word rotates every 4 seconds from the pool for the current phase, so it reads as
alive without ever describing the wrong activity.

| Phase | Trigger | Pool |
|---|---|---|
| thinking | default, and after `leave_tool()` | Thinking, Noodling, Pondering, Mulling |
| creating | `edit_file` with empty `find_str` | Drafting, Writing, Composing |
| patching | `edit_file` with non-empty `find_str` | Patching, Splicing, Revising |
| running | `run_sandboxed_code` | Running, Executing |
| installing | `install_package` | Fetching, Installing |
| reading | `read_file_content` | Reading, Skimming |
| listing | `list_directory` | Looking around, Browsing |
| checking | `validate_python_syntax`, `validate_imports` | Checking, Vetting |
| retrying | `stats.has_recent_error` is true | Regrouping, Rethinking |

`retrying` takes precedence over `thinking` but not over an active tool phase.
`handle()` already receives `session`, so the region reads `session.stats()` for
`has_recent_error`, `total_tokens_used` and `retry_count` without new events.

An unrecognised tool name falls back to the `thinking` pool and the raw tool
name as the step verb, so adding a tool to `src/tools` never crashes the
renderer.

### Status line

```
⠋ Noodling…  12s · 4.1k tokens · ctrl+c to interrupt
```

Elapsed is whole seconds from `started_at`. Tokens reuse the existing
`format_tokens`. When `retry_count` is non-zero the line appends
`retry 2/3` in `cf.warn`.

While a tool is active the region renders two rows: the pending step row (same
layout as a finished one, with the spinner in place of the glyph) above the
status line.

### Step rows

One line per completed step, four columns:

```
✓ create   fibonacci.py          +24
✓ patch    fibonacci.py          +3/-1
✓ run      pytest.py             5 passed in 0.31s
✗ run      broken.py             exit 1
```

- **glyph** — `✓` in `cf.ok` when `ToolResult.ok`, `✗` in `cf.fail` otherwise.
- **verb** — short label per tool: `create`, `patch`, `run`, `install`, `read`,
  `list`, `check`. Padded to 8 characters.
- **target** — the existing `_format_args` logic, which already prefers
  `filename`, `path`, `package_name` over dumping contents. The validators take
  a raw `code` argument and have no target; they render `—`.
- **metric** — see below.

### Metric derivation

The tools return plain strings, not structured data, so `ToolResult.data` is
`None` for everything except `run_sandboxed_code`. Line counts must be computed
by the renderer from the arguments it already stashes in `self._call_args` at
`ToolCallStarted`:

| Tool | Metric | Source |
|---|---|---|
| `edit_file`, create | `+N` | `len(args["replace_str"].splitlines())` |
| `edit_file`, patch | `+N/-M` | line counts of `replace_str` vs `find_str` |
| `run_sandboxed_code` | `exit {code}`, then ` · {last non-empty stdout line}` clipped to 40 chars when stdout is non-empty | `data["exit_code"]`, `data["stdout"]` |
| `read_file_content` | `N lines` | `len(event.content.splitlines())` |
| anything else, or `edit_file` with no stashed args | first line of `event.content`, clipped to 40 chars | current behaviour at `console.py:160` |

For `pytest -q` the last stdout line happens to read `5 passed in 0.31s`. This
is a generic "last line of output" rule, not pytest-specific parsing.

The final fallback row matters for more than unknown tools: `ToolResult` can
arrive without a matching `ToolCallStarted` ever having been seen by this
renderer — on a resumed session, or in a unit test — and the tool's own return
string (`Success: File created.`) is the only honest thing left to show.

`_call_args` currently grows for the lifetime of the session because nothing
removes entries. `_render_result` pops the entry it consumes.

### Failures still expand

When `ToolResult.ok` is false the renderer prints the step row *and* keeps the
existing `Panel` of stdout/stderr, clipped to `MAX_OUTPUT_LINES`. This is the
one case where collapsing is wrong — the user needs the traceback.

### Prose

`_stream` keeps appending tokens to a buffer and no longer touches `Live`.
Prose prints once, at `RunFinished`, for both `completed` and
`awaiting_approval` reasons — omitting `awaiting_approval` would lose the
model's explanation of the edit it is asking permission for.

A `strip_code_fences(text)` helper replaces each fenced block with a single
reference line:

```
▸ fibonacci.py (24 lines)
```

The filename comes from the nearest preceding `CodeGenerated` event for that
turn when one exists; otherwise the fence's language tag is used
(`▸ python (24 lines)`). `AssistantMessage` goes through the same helper.

### Status bar at turn end

`_status_line` keeps its current contents (turn count, tokens, retries) and is
printed after the prose, unchanged.

## Why no event changes are needed

`Session._run` streams in `stream_mode=["messages", "updates"]`. Everything the
status region needs is already derivable:

- Phase comes from `ToolCallStarted` / `ToolResult` pairs. `ToolCallStarted` is
  emitted from the `updates` stream after the agent node returns, which is
  before the tool node executes — so the "running" row is correctly timed.
- Whether an `edit_file` creates or patches is visible in `args["find_str"]`.
- Live token counts, retry counts and `has_recent_error` come from
  `session.stats()`, and `handle()` already receives `session`.
- The gap between Enter and the first token is covered by starting the region in
  `drive()` instead of on an event.

## Testing

`tests/test_ui.py` keeps its `io.StringIO` + `force_terminal=False` fixture;
that already prevents Live from animating under pytest.

New tests:

A `Live(transient=True)` on a non-terminal console prints nothing at all — not
even on stop. So the status region is invisible to the existing `StringIO`
assertions, and needs its own tests that render it directly.

- `StatusRegion.render()` is deterministic with an injected clock alone: the
  word is `pool[elapsed // 4 % len(pool)]`, so no injected word picker is
  needed. Assert the word for each phase, elapsed formatting, and the retry
  suffix.
- Phase transitions: `enter_tool("edit_file", {"find_str": ""})` selects the
  creating pool; non-empty `find_str` selects patching; an unknown tool name
  falls back to thinking without raising.
- Step row metrics: one test per row of the metric table.
- `strip_code_fences` replaces a fenced block, leaves inline backticks alone,
  and handles an unterminated fence.
- Prose is flushed on `RunFinished(reason="awaiting_approval")`.
- `_call_args` is empty after a tool result is rendered.

Existing tests that change by design:

- `test_assistant_message` — asserts on prose without firing `RunFinished`, so
  buffered prose never flushes. Add the `RunFinished`.

Three more because `ToolCallStarted` no longer writes to scrollback, so a test
that fires only that event now sees nothing:

- `test_tool_call` — add the matching `ToolResult` and assert on the step row.
  The `"edit_file" in output` assertion becomes the step verb, `create`.
- `test_tool_call_shows_filename_not_contents` — add the matching `ToolResult`.
  Its intent (filename shown, 200 lines of content not shown) is preserved by
  the step row.
- `test_long_args_are_summarized` — add the matching `ToolResult`. The step row
  reuses `_format_args`, so `code=<300 chars>` still appears.

Four tests that looked at risk survive unchanged, and the implementation must
keep them that way:

- `test_assistant_tokens_are_flushed` already fires `RunFinished` after the
  tokens, which is exactly when buffered prose flushes.
- `test_execution_result_shows_output_and_exit_code` asserts `42` and `exit 0` —
  both present in the collapsed metric `exit 0 · 42`.
- `test_failed_result_is_shown` asserts on the panel, which failures keep.
- `test_simple_result_line` fires a `ToolResult` with no preceding
  `ToolCallStarted`, which is what the content fallback row covers.
- `test_every_event_type_renders` uses the tool name `t`, which is what the
  unknown-tool fallback covers.

The remaining tests in `test_ui.py` are unaffected. `test_e2e.py` does not touch
the renderer.

## Risks

- **Windows terminals.** The braille spinner and `✓`/`✗` need a UTF-8 capable
  console. Rich degrades box characters but not arbitrary text, so the glyph set
  falls back to `+`/`!` and `-` for the spinner when
  `console.options.legacy_windows` is set.
- **Interleaving.** `console.print` while a `Live` is active is supported by
  Rich, but ordering bugs show up as duplicated or erased lines. The lifecycle
  table above is the contract; the `try/finally` in `drive()` is what keeps a
  crash from leaving the terminal with a hidden cursor.
