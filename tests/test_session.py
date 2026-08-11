import uuid
import pytest
from langchain_core.messages import AIMessage

import src.agent.nodes as nodes_module
import src.sandbox.paths as paths_module
from src.service import Session, list_sessions
from src.service.events import (
    ApprovalRequest,
    ApprovalRequested,
    AssistantToken,
    CodeGenerated,
    RunFinished,
    ToolCallStarted,
    ToolResult,
)


@pytest.fixture(autouse=True)
def tmp_workspace_root(tmp_path, monkeypatch):
    monkeypatch.setattr(paths_module, "WORKSPACE_ROOT", tmp_path)
    return tmp_path


class ScriptedLLM:
    """Replays a fixed list of AIMessages, one per call_model invocation."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    def invoke(self, _prompt):
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return response


def ai(content="", tool_calls=None, tokens=10):
    msg = AIMessage(content=content, response_metadata={"token_usage": {"total_tokens": tokens}})
    msg.id = str(uuid.uuid4())
    msg.tool_calls = tool_calls or []
    return msg


def edit_call(filename="hello.py", find_str="", replace_str="print('hi')", call_id="tc1"):
    return {
        "id": call_id,
        "name": "edit_file",
        "args": {"filename": filename, "find_str": find_str, "replace_str": replace_str},
    }


@pytest.fixture
def scripted(monkeypatch):
    def install(*responses):
        llm = ScriptedLLM(*responses)
        monkeypatch.setattr(nodes_module, "llm", llm)
        return llm

    return install


def drain(events):
    return list(events)


def of_type(events, kind):
    return [e for e in events if isinstance(e, kind)]


class TestSendWithoutApproval:
    def test_plain_reply_completes(self, scripted):
        scripted(ai(content="hello there"))
        session = Session()
        events = drain(session.send("hi"))

        assert not session.is_paused()
        finished = of_type(events, RunFinished)[-1]
        assert finished.reason == "completed"

    def test_emits_assistant_text_once(self, scripted):
        scripted(ai(content="hello there"))
        events = drain(Session().send("hi"))
        text = "".join(e.text for e in of_type(events, AssistantToken))
        assert text == "hello there"

    def test_reports_token_and_turn_stats(self, scripted):
        scripted(ai(content="done", tokens=42))
        events = drain(Session().send("hi"))
        stats = of_type(events, RunFinished)[-1].stats
        assert stats.total_tokens_used == 42
        assert stats.turn_count == 1
        assert stats.token_budget > 0


class TestApprovalPause:
    def test_pauses_before_side_effects(self, scripted, tmp_workspace_root):
        scripted(ai(tool_calls=[edit_call()]))
        session = Session()
        events = drain(session.send("write hello"))

        assert session.is_paused()
        assert of_type(events, RunFinished)[-1].reason == "awaiting_approval"
        assert not (tmp_workspace_root / session.session_id / "hello.py").exists()

    def test_emits_the_pending_request(self, scripted):
        scripted(ai(tool_calls=[edit_call()]))
        session = Session()
        events = drain(session.send("write hello"))

        requested = of_type(events, ApprovalRequested)[0]
        assert len(requested.requests) == 1
        assert requested.requests[0].tool == "edit_file"
        assert requested.requests[0].tool_call_id == "tc1"

    def test_emits_tool_call_started(self, scripted):
        scripted(ai(tool_calls=[edit_call()]))
        events = drain(Session().send("write hello"))
        started = of_type(events, ToolCallStarted)[0]
        assert started.name == "edit_file"
        assert started.args["filename"] == "hello.py"

    def test_pending_approval_survives_a_new_session_object(self, scripted):
        scripted(ai(tool_calls=[edit_call()]))
        session = Session()
        drain(session.send("write hello"))

        reopened = Session(session.session_id)
        assert reopened.is_paused()
        assert reopened.pending_approval()[0].tool_call_id == "tc1"

    def test_resume_rejected_when_not_paused(self, scripted):
        scripted(ai(content="hi"))
        session = Session()
        drain(session.send("hi"))
        with pytest.raises(RuntimeError, match="not awaiting approval"):
            drain(session.resume(True))


class TestResume:
    def test_approval_applies_the_edit(self, scripted, tmp_workspace_root):
        scripted(ai(tool_calls=[edit_call()]), ai(content="saved it"))
        session = Session()
        drain(session.send("write hello"))
        events = drain(session.resume(True))

        target = tmp_workspace_root / session.session_id / "hello.py"
        assert target.read_text() == "print('hi')"
        assert not session.is_paused()
        assert of_type(events, RunFinished)[-1].reason == "completed"

    def test_denial_leaves_the_file_alone(self, scripted, tmp_workspace_root):
        scripted(ai(tool_calls=[edit_call()]), ai(content="ok, skipped"))
        session = Session()
        drain(session.send("write hello"))
        events = drain(session.resume(False))

        assert not (tmp_workspace_root / session.session_id / "hello.py").exists()
        result = of_type(events, ToolResult)[0]
        assert result.ok is False
        assert "CANCELLED" in result.content

    def test_approval_emits_tool_result_and_code(self, scripted):
        scripted(ai(tool_calls=[edit_call()]), ai(content="saved it"))
        session = Session()
        drain(session.send("write hello"))
        events = drain(session.resume(True))

        assert of_type(events, ToolResult)[0].ok is True
        generated = of_type(events, CodeGenerated)
        assert generated and generated[0].filename == "hello.py"


class TestWorkspace:
    def test_files_lists_nested_paths(self, scripted, tmp_workspace_root):
        session = Session()
        root = tmp_workspace_root / session.session_id
        (root / "sub").mkdir(parents=True)
        (root / "a.py").write_text("x")
        (root / "sub" / "b.py").write_text("y")

        assert session.files() == ["a.py", "sub/b.py"]

    def test_read_file(self, tmp_workspace_root):
        session = Session()
        session.workspace.joinpath("a.py").write_text("x = 1")
        assert session.read_file("a.py") == "x = 1"

    def test_read_file_rejects_escape(self):
        with pytest.raises(ValueError, match="Path escapes workspace"):
            Session().read_file("../../etc/passwd")

    def test_sessions_do_not_share_files(self, tmp_workspace_root):
        one, two = Session(), Session()
        one.workspace.joinpath("mine.py").write_text("x")
        assert one.files() == ["mine.py"]
        assert two.files() == []


class TestPreview:
    def test_new_file_preview(self):
        session = Session()
        request = ApprovalRequest("1", "edit_file", {"filename": "a.py", "find_str": "", "replace_str": "x = 1"})
        preview = session.preview(request)
        assert preview.is_new is True
        assert preview.before == ""
        assert preview.after == "x = 1"

    def test_existing_file_preview(self):
        session = Session()
        session.workspace.joinpath("a.py").write_text("x = 1")
        request = ApprovalRequest("1", "edit_file", {"filename": "a.py", "find_str": "x = 1", "replace_str": "x = 2"})
        preview = session.preview(request)
        assert preview.is_new is False
        assert preview.applies is True
        assert preview.after == "x = 2"

    def test_flags_edit_that_will_not_match(self):
        session = Session()
        session.workspace.joinpath("a.py").write_text("x = 1")
        request = ApprovalRequest("1", "edit_file", {"filename": "a.py", "find_str": "nope", "replace_str": "y"})
        assert session.preview(request).applies is False

    def test_no_preview_for_non_edit_tools(self):
        request = ApprovalRequest("1", "run_sandboxed_code", {"filename": "a.py"})
        assert Session().preview(request) is None

    def test_no_preview_for_escaping_path(self):
        request = ApprovalRequest("1", "edit_file", {"filename": "../../evil.py", "find_str": "", "replace_str": "x"})
        assert Session().preview(request) is None


class TestListSessions:
    def test_includes_a_session_that_has_run(self, scripted):
        scripted(ai(content="hi"))
        session = Session()
        drain(session.send("hi"))

        ids = [info.session_id for info in list_sessions()]
        assert session.session_id in ids

    def test_reports_workspace_files(self, scripted):
        scripted(ai(content="hi"))
        session = Session()
        drain(session.send("hi"))
        session.workspace.joinpath("a.py").write_text("x")

        info = next(i for i in list_sessions() if i.session_id == session.session_id)
        assert info.files == ["a.py"]
        assert info.has_workspace is True


class TestHistory:
    def test_history_persists_across_session_objects(self, scripted):
        scripted(ai(content="first reply"))
        session = Session()
        drain(session.send("remember this"))

        reopened = Session(session.session_id)
        contents = [str(m.content) for m in reopened.history()]
        assert "remember this" in contents
        assert "first reply" in contents

    def test_unknown_session_is_empty(self):
        session = Session("never-used")
        assert session.exists() is False
        assert session.history() == []
