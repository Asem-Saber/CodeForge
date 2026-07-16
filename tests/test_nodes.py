import json
import pytest
from unittest.mock import MagicMock
from langchain_core.messages import AIMessage, ToolMessage, HumanMessage

from src.agent.state import AgentState
from src.agent.nodes import (
    process_execution,
    retry_router,
    validation_gate,
    _block_all_tool_calls,
    route_after_agent,
    route_after_validation,
    route_after_retry,
)
from src.config import MAX_TURNS, MAX_RETRIES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_ai_message_with_tool_calls(tool_calls):
    msg = AIMessage(content="")
    msg.tool_calls = tool_calls
    return msg


def make_tool_message(tool_call_id, content):
    return ToolMessage(content=content, tool_call_id=tool_call_id)


# ---------------------------------------------------------------------------
# route_after_agent
# ---------------------------------------------------------------------------

class TestRouteAfterAgent:
    def test_ends_when_max_turns_reached(self):
        state = {"messages": [AIMessage(content="done")], "turn_count": MAX_TURNS}
        assert route_after_agent(state) == "__end__"

    def test_ends_when_no_tool_calls(self):
        state = {"messages": [AIMessage(content="just text")], "turn_count": 0}
        assert route_after_agent(state) == "__end__"

    def test_routes_to_validation_gate_with_tool_calls(self):
        ai = make_ai_message_with_tool_calls([
            {"id": "1", "name": "edit_file", "args": {"filename": "x.py", "find_str": "", "replace_str": "pass"}}
        ])
        state = {"messages": [ai], "turn_count": 0}
        assert route_after_agent(state) == "validation_gate"


# ---------------------------------------------------------------------------
# route_after_validation
# ---------------------------------------------------------------------------

class TestRouteAfterValidation:
    def test_routes_to_agent_when_blocked(self):
        state = {"messages": [ToolMessage(content="BLOCKED", tool_call_id="1")]}
        assert route_after_validation(state) == "agent"

    def test_routes_to_tools_when_passed(self):
        ai = make_ai_message_with_tool_calls([{"id": "1", "name": "edit_file", "args": {}}])
        state = {"messages": [ai]}
        assert route_after_validation(state) == "tools"


# ---------------------------------------------------------------------------
# route_after_retry
# ---------------------------------------------------------------------------

class TestRouteAfterRetry:
    def test_continues_when_no_error(self):
        state = {"has_recent_error": False, "retry_count": 0}
        assert route_after_retry(state) == "agent"

    def test_retries_when_under_limit(self):
        state = {"has_recent_error": True, "retry_count": 1}
        assert route_after_retry(state) == "agent"

    def test_ends_when_retries_exhausted(self):
        state = {"has_recent_error": True, "retry_count": MAX_RETRIES + 1}
        assert route_after_retry(state) == "__end__"


# ---------------------------------------------------------------------------
# _block_all_tool_calls
# ---------------------------------------------------------------------------

class TestBlockAllToolCalls:
    def test_returns_tool_messages_for_each_call(self):
        tool_calls = [
            {"id": "a", "name": "run_sandboxed_code", "args": {}},
            {"id": "b", "name": "edit_file", "args": {}},
        ]
        result = _block_all_tool_calls(tool_calls, "Validation failed")
        messages = result["messages"]
        assert len(messages) == 2
        assert messages[0].content == "Validation failed"
        assert "SKIPPED" in messages[1].content

    def test_tool_call_ids_match(self):
        tool_calls = [{"id": "x", "name": "run_sandboxed_code", "args": {}}]
        result = _block_all_tool_calls(tool_calls, "error")
        assert result["messages"][0].tool_call_id == "x"


# ---------------------------------------------------------------------------
# validation_gate
# ---------------------------------------------------------------------------

class TestValidationGate:
    def test_passes_when_no_tool_calls(self):
        state = {"messages": [AIMessage(content="hello")]}
        assert validation_gate(state) == {}

    def test_passes_when_no_run_sandboxed_code(self):
        ai = make_ai_message_with_tool_calls([
            {"id": "1", "name": "edit_file", "args": {"filename": "x.py"}}
        ])
        state = {"messages": [ai]}
        assert validation_gate(state) == {}

    def test_blocks_file_with_syntax_error(self, tmp_path, monkeypatch):
        import src.sandbox.paths as p
        monkeypatch.setattr(p, "WORKSPACE", tmp_path)
        (tmp_path / "bad.py").write_text("def foo(\n")

        ai = make_ai_message_with_tool_calls([
            {"id": "1", "name": "run_sandboxed_code", "args": {"filename": "bad.py"}}
        ])
        state = {"messages": [ai]}
        result = validation_gate(state)
        assert "messages" in result
        assert "BLOCKED" in result["messages"][0].content
        assert "Syntax error" in result["messages"][0].content

    def test_passes_valid_file(self, tmp_path, monkeypatch):
        import src.sandbox.paths as p
        monkeypatch.setattr(p, "WORKSPACE", tmp_path)
        (tmp_path / "good.py").write_text("print('hello')")

        ai = make_ai_message_with_tool_calls([
            {"id": "1", "name": "run_sandboxed_code", "args": {"filename": "good.py"}}
        ])
        state = {"messages": [ai]}
        assert validation_gate(state) == {}

    def test_skips_missing_file(self):
        ai = make_ai_message_with_tool_calls([
            {"id": "1", "name": "run_sandboxed_code", "args": {"filename": "nonexistent.py"}}
        ])
        state = {"messages": [ai]}
        assert validation_gate(state) == {}


# ---------------------------------------------------------------------------
# process_execution
# ---------------------------------------------------------------------------

class TestProcessExecution:
    def test_parses_json_success(self):
        ai = make_ai_message_with_tool_calls([
            {"id": "run1", "name": "run_sandboxed_code", "args": {"filename": "test.py", "language": "python"}}
        ])
        result_json = json.dumps({"stdout": "42", "stderr": "", "exit_code": 0, "error": None, "artifacts": []})
        tool_msg = make_tool_message("run1", result_json)

        state = {
            "messages": [ai, tool_msg],
            "generated_code": [],
            "execution_results": [],
            "execution_errors": [],
        }
        result = process_execution(state)
        assert len(result["execution_results"]) == 1
        assert len(result["execution_errors"]) == 0
        assert result["execution_results"][0]["exit_code"] == 0
        assert result["has_recent_error"] is False

    def test_parses_json_failure(self):
        ai = make_ai_message_with_tool_calls([
            {"id": "run2", "name": "run_sandboxed_code", "args": {"filename": "fail.py", "language": "python"}}
        ])
        result_json = json.dumps({"stdout": "", "stderr": "NameError", "exit_code": 1, "error": "NameError", "artifacts": []})
        tool_msg = make_tool_message("run2", result_json)

        state = {
            "messages": [ai, tool_msg],
            "generated_code": [],
            "execution_results": [],
            "execution_errors": [],
        }
        result = process_execution(state)
        assert len(result["execution_errors"]) == 1
        assert result["has_recent_error"] is True

    def test_handles_non_json_content(self):
        ai = make_ai_message_with_tool_calls([
            {"id": "run3", "name": "run_sandboxed_code", "args": {"filename": "x.py", "language": "python"}}
        ])
        tool_msg = make_tool_message("run3", "some plain text error")

        state = {
            "messages": [ai, tool_msg],
            "generated_code": [],
            "execution_results": [],
            "execution_errors": [],
        }
        result = process_execution(state)
        assert len(result["execution_errors"]) == 1
        assert result["execution_errors"][0]["exit_code"] == 1

    def test_tracks_edit_file(self):
        ai = make_ai_message_with_tool_calls([
            {"id": "edit1", "name": "edit_file", "args": {"filename": "app.py", "find_str": "", "replace_str": "print(1)"}}
        ])
        tool_msg = make_tool_message("edit1", "Success: File created.")

        state = {
            "messages": [ai, tool_msg],
            "generated_code": [],
            "execution_results": [],
            "execution_errors": [],
        }
        result = process_execution(state)
        assert len(result["generated_code"]) == 1
        assert result["generated_code"][0]["filename"] == "app.py"
        assert result["generated_code"][0]["code"] == "print(1)"

    def test_skips_already_processed(self):
        ai = make_ai_message_with_tool_calls([
            {"id": "old1", "name": "edit_file", "args": {"filename": "a.py", "find_str": "", "replace_str": "x"}}
        ])
        tool_msg = make_tool_message("old1", "Success: File created.")

        state = {
            "messages": [ai, tool_msg],
            "generated_code": [{"tool_call_id": "old1", "code": "x", "language": "python", "filename": "a.py", "execution_id": None}],
            "execution_results": [],
            "execution_errors": [],
        }
        result = process_execution(state)
        assert len(result["generated_code"]) == 0


# ---------------------------------------------------------------------------
# retry_router
# ---------------------------------------------------------------------------

class TestRetryRouter:
    def test_resets_count_on_success(self):
        state = {"has_recent_error": False, "retry_count": 2}
        result = retry_router(state)
        assert result["retry_count"] == 0

    def test_increments_on_error(self):
        state = {"has_recent_error": True, "retry_count": 0}
        result = retry_router(state)
        assert result["retry_count"] == 1

    def test_stops_when_exhausted(self):
        state = {"has_recent_error": True, "retry_count": MAX_RETRIES}
        result = retry_router(state)
        assert result["retry_count"] == MAX_RETRIES + 1
        assert len(result["messages"]) == 1
        assert "exhausted" in result["messages"][0].content

    def test_no_message_when_retrying(self):
        state = {"has_recent_error": True, "retry_count": 0}
        result = retry_router(state)
        assert "messages" not in result
