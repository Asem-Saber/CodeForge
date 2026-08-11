import json
import pytest
from unittest.mock import MagicMock
from langchain_core.messages import AIMessage, ToolMessage, HumanMessage

import src.agent.nodes as nodes_module
import src.sandbox.paths as paths_module
from src.agent.state import AgentState
from src.agent.nodes import (
    process_execution,
    retry_router,
    validation_gate,
    approval_gate,
    _block_all_tool_calls,
    _resolve_approval,
    _describe_tool_call,
    route_after_agent,
    route_after_validation,
    route_after_approval,
    route_after_retry,
)
from src.config import MAX_TURNS, MAX_RETRIES

SESSION = "test-session"
CONFIG = {"configurable": {"thread_id": SESSION}}


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

    def test_routes_to_approval_gate_when_passed(self):
        ai = make_ai_message_with_tool_calls([{"id": "1", "name": "edit_file", "args": {}}])
        state = {"messages": [ai]}
        assert route_after_validation(state) == "approval_gate"


# route_after_approval

class TestRouteAfterApproval:
    def test_routes_to_agent_when_denied(self):
        state = {"messages": [ToolMessage(content="CANCELLED", tool_call_id="1")]}
        assert route_after_approval(state) == "agent"

    def test_routes_to_tools_when_approved(self):
        ai = make_ai_message_with_tool_calls([{"id": "1", "name": "edit_file", "args": {}}])
        state = {"messages": [ai]}
        assert route_after_approval(state) == "tools"


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
        monkeypatch.setattr(paths_module, "WORKSPACE_ROOT", tmp_path)
        paths_module.session_workspace(SESSION).joinpath("bad.py").write_text("def foo(\n")

        ai = make_ai_message_with_tool_calls([
            {"id": "1", "name": "run_sandboxed_code", "args": {"filename": "bad.py"}}
        ])
        state = {"messages": [ai]}
        result = validation_gate(state, CONFIG)
        assert "messages" in result
        assert "BLOCKED" in result["messages"][0].content
        assert "Syntax error" in result["messages"][0].content

    def test_passes_valid_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(paths_module, "WORKSPACE_ROOT", tmp_path)
        paths_module.session_workspace(SESSION).joinpath("good.py").write_text("print('hello')")

        ai = make_ai_message_with_tool_calls([
            {"id": "1", "name": "run_sandboxed_code", "args": {"filename": "good.py"}}
        ])
        state = {"messages": [ai]}
        assert validation_gate(state, CONFIG) == {}

    def test_reads_the_calling_session_workspace(self, tmp_path, monkeypatch):
        monkeypatch.setattr(paths_module, "WORKSPACE_ROOT", tmp_path)
        # Broken file belongs to another session, so this session must not see it.
        paths_module.session_workspace("other").joinpath("bad.py").write_text("def foo(\n")

        ai = make_ai_message_with_tool_calls([
            {"id": "1", "name": "run_sandboxed_code", "args": {"filename": "bad.py"}}
        ])
        assert validation_gate({"messages": [ai]}, CONFIG) == {}

    def test_skips_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(paths_module, "WORKSPACE_ROOT", tmp_path)
        ai = make_ai_message_with_tool_calls([
            {"id": "1", "name": "run_sandboxed_code", "args": {"filename": "nonexistent.py"}}
        ])
        state = {"messages": [ai]}
        assert validation_gate(state, CONFIG) == {}


# approval_gate

class TestApprovalGate:
    @pytest.fixture
    def approve(self, monkeypatch):
        """Stand in for interrupt(), capturing the payload and replying with a decision."""
        captured = {}

        def fake_interrupt_factory(decision):
            def fake_interrupt(payload):
                captured["payload"] = payload
                return decision
            return fake_interrupt

        def install(decision):
            monkeypatch.setattr(nodes_module, "interrupt", fake_interrupt_factory(decision))
            return captured

        return install

    def test_passes_when_no_tool_calls(self, approve):
        approve(True)
        state = {"messages": [AIMessage(content="hello")]}
        assert approval_gate(state) == {}

    def test_passes_when_no_approval_needed(self, approve):
        approve(False)
        ai = make_ai_message_with_tool_calls([
            {"id": "1", "name": "read_file_content", "args": {"path": "a.py"}}
        ])
        # Read-only tools must not pause the graph at all.
        assert approval_gate({"messages": [ai]}) == {}

    def test_proceeds_when_approved(self, approve):
        approve(True)
        ai = make_ai_message_with_tool_calls([
            {"id": "1", "name": "edit_file", "args": {"filename": "a.py", "find_str": "", "replace_str": "x = 1"}}
        ])
        assert approval_gate({"messages": [ai]}) == {}

    def test_cancels_when_denied(self, approve):
        approve(False)
        ai = make_ai_message_with_tool_calls([
            {"id": "1", "name": "edit_file", "args": {"filename": "a.py", "find_str": "", "replace_str": "x = 1"}}
        ])
        result = approval_gate({"messages": [ai]})
        assert len(result["messages"]) == 1
        assert "CANCELLED" in result["messages"][0].content
        assert result["messages"][0].tool_call_id == "1"

    def test_denial_answers_every_pending_tool_call(self, approve):
        approve(False)
        ai = make_ai_message_with_tool_calls([
            {"id": "1", "name": "edit_file", "args": {"filename": "a.py", "find_str": "", "replace_str": "x"}},
            {"id": "2", "name": "read_file_content", "args": {"path": "b.py"}},
        ])
        result = approval_gate({"messages": [ai]})
        # Every tool call needs a reply or the model sees a dangling call.
        assert {m.tool_call_id for m in result["messages"]} == {"1", "2"}
        assert "CANCELLED" in result["messages"][0].content
        assert "SKIPPED" in result["messages"][1].content

    def test_partial_denial_blocks_the_batch(self, approve):
        approve({"1": True, "2": False})
        ai = make_ai_message_with_tool_calls([
            {"id": "1", "name": "edit_file", "args": {"filename": "a.py", "find_str": "", "replace_str": "x"}},
            {"id": "2", "name": "run_sandboxed_code", "args": {"filename": "a.py"}},
        ])
        result = approval_gate({"messages": [ai]})
        assert len(result["messages"]) == 2
        assert "SKIPPED" in result["messages"][0].content
        assert "CANCELLED" in result["messages"][1].content

    def test_payload_describes_pending_actions(self, approve):
        captured = approve(True)
        ai = make_ai_message_with_tool_calls([
            {"id": "1", "name": "edit_file", "args": {"filename": "a.py", "find_str": "", "replace_str": "x = 1"}},
            {"id": "2", "name": "read_file_content", "args": {"path": "b.py"}},
        ])
        approval_gate({"messages": [ai]})
        payload = captured["payload"]
        assert payload["type"] == "approval_request"
        assert len(payload["requests"]) == 1
        assert payload["requests"][0]["tool_call_id"] == "1"
        assert "a.py" in payload["requests"][0]["description"]


# _resolve_approval

class TestResolveApproval:
    PENDING = [{"id": "1", "name": "edit_file", "args": {}}]

    def test_true_approves(self):
        assert _resolve_approval(True, self.PENDING) == {"1": True}

    def test_false_denies(self):
        assert _resolve_approval(False, self.PENDING) == {"1": False}

    @pytest.mark.parametrize("answer", ["y", "yes", "Approve", " TRUE "])
    def test_affirmative_strings(self, answer):
        assert _resolve_approval(answer, self.PENDING) == {"1": True}

    @pytest.mark.parametrize("answer", ["n", "no", "", "maybe"])
    def test_other_strings_deny(self, answer):
        assert _resolve_approval(answer, self.PENDING) == {"1": False}

    def test_per_call_mapping(self):
        pending = [{"id": "1"}, {"id": "2"}]
        assert _resolve_approval({"1": True, "2": False}, pending) == {"1": True, "2": False}

    def test_mapping_defaults_missing_ids_to_denied(self):
        assert _resolve_approval({}, self.PENDING) == {"1": False}

    @pytest.mark.parametrize("value", [None, 1, object()])
    def test_unrecognized_values_fail_closed(self, value):
        assert _resolve_approval(value, self.PENDING) == {"1": False}


# _describe_tool_call

class TestDescribeToolCall:
    def test_create_reads_as_create(self):
        tc = {"name": "edit_file", "args": {"filename": "a.py", "find_str": "", "replace_str": "x = 1"}}
        description = _describe_tool_call(tc)
        assert description.startswith("create a.py")
        assert "x = 1" in description

    def test_modify_reads_as_modify(self):
        tc = {"name": "edit_file", "args": {"filename": "a.py", "find_str": "x", "replace_str": "y"}}
        assert _describe_tool_call(tc).startswith("modify a.py")

    def test_truncates_long_bodies(self):
        tc = {"name": "edit_file", "args": {"filename": "a.py", "find_str": "", "replace_str": "A" * 5000}}
        description = _describe_tool_call(tc)
        assert "[...truncated...]" in description
        assert len(description) < 600

    def test_describes_sandbox_run(self):
        tc = {"name": "run_sandboxed_code", "args": {"filename": "a.py", "language": "python"}}
        assert "run a.py" in _describe_tool_call(tc)


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
