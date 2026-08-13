"""End-to-end runs through the graph.

Two tiers, both marked `e2e`:

* `TestGraphAgainstRealSandbox` keeps the scripted LLM but lets the tools hit a
  real container, covering approval -> resume -> file write -> execution as one
  path. Needs a daemon and the sandbox image; skips without them.
* `TestModelProtocol` goes the other way: a real `ChatOpenAI` talking HTTP to a
  stub completions server. That covers the wire format the scripted LLM skips
  entirely -- tool schema serialisation, tool-call parsing, token accounting.
  Needs neither Docker nor an API key.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from langchain_openai import ChatOpenAI

import src.agent.nodes as nodes_module
import src.sandbox.manager as manager
import src.sandbox.paths as paths_module
from src.service import Session
from src.service.events import (
    ApprovalRequested,
    AssistantToken,
    RunFinished,
    ToolResult,
)
from src.tools import tools
from tests.test_session import ScriptedLLM, ai, drain, edit_call, of_type

pytestmark = pytest.mark.e2e


def run_call(filename="hello.py", call_id="tc2"):
    return {"id": call_id, "name": "run_sandboxed_code", "args": {"filename": filename}}


def write_and_run(code, filename="hello.py"):
    """Script the model through: write the file, run it, report back."""
    return (
        ai(tool_calls=[edit_call(filename=filename, replace_str=code)]),
        ai(tool_calls=[run_call(filename)]),
        ai(content="all done"),
    )


def tool_results(events, name):
    return [e for e in of_type(events, ToolResult) if e.name == name]


class TestGraphAgainstRealSandbox:
    @pytest.fixture(autouse=True)
    def _sandbox(self, docker_client, sandbox_workspace_root, closes_sandboxes):
        self.workspace_root = sandbox_workspace_root

    @pytest.fixture
    def scripted(self, monkeypatch):
        def install(*responses):
            monkeypatch.setattr(nodes_module, "llm", ScriptedLLM(*responses))

        return install

    def test_writes_then_executes_the_file(self, scripted):
        scripted(*write_and_run("print('hi from the sandbox')"))
        session = Session()

        drain(session.send("write and run hello"))
        drain(session.resume(True))        
        events = drain(session.resume(True))  

        assert (self.workspace_root / session.session_id / "hello.py").exists()
        result = json.loads(tool_results(events, "run_sandboxed_code")[0].content)
        assert result["exit_code"] == 0
        assert "hi from the sandbox" in result["stdout"]
        assert of_type(events, RunFinished)[-1].reason == "completed"

    def test_container_belongs_to_the_calling_session(self, scripted):
        """The run config has to survive the whole graph, not just the first hop."""
        scripted(*write_and_run("print('ok')"))
        session = Session()

        drain(session.send("write and run hello"))
        drain(session.resume(True))
        drain(session.resume(True))

        container = manager._containers[session.session_id]
        assert container.labels["codeforge.session"] == session.session_id

    def test_validation_gate_blocks_on_the_sandbox_environment(self, scripted):
        """langchain resolves on the host but not in the sandbox image.

        _check_imports_raw silently falls back to a host check when the sandbox
        is unreachable, so a pass here would mean the gate stopped consulting
        the container.
        """
        scripted(*write_and_run("import langchain\nprint('never runs')"))
        session = Session()

        drain(session.send("write and run hello"))
        events = drain(session.resume(True))  

        blocked = [e for e in of_type(events, ToolResult) if "BLOCKED" in e.content]
        assert blocked, "expected the validation gate to block the run"
        assert "langchain" in blocked[0].content
        assert not session.is_paused(), "a blocked run should not ask for approval"


def _completion(message, total_tokens=11):
    return {
        "id": "chatcmpl-stub",
        "object": "chat.completion",
        "created": 0,
        "model": "stub-model",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": 1,
            "completion_tokens": total_tokens - 1,
            "total_tokens": total_tokens,
        },
    }


def text_completion(text, total_tokens=11):
    return _completion({"role": "assistant", "content": text}, total_tokens)


def tool_call_completion(name, args, call_id="tc1"):
    payload = _completion(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
            ],
        }
    )
    payload["choices"][0]["finish_reason"] = "tool_calls"
    return payload


def _as_chunks(payload):
    """Re-cut a completion payload as the SSE chunks the client expects.

    call_model uses invoke(), but LangGraph's "messages" stream mode turns that
    into a streaming request, so this is the path that actually runs.
    """
    base = {
        "id": payload["id"],
        "object": "chat.completion.chunk",
        "created": payload["created"],
        "model": payload["model"],
    }
    message = payload["choices"][0]["message"]
    finish_reason = payload["choices"][0]["finish_reason"]

    def frame(delta, finish=None):
        return {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}

    yield frame({"role": "assistant"})
    if message.get("content"):
        yield frame({"content": message["content"]})
    for index, call in enumerate(message.get("tool_calls") or []):
        yield frame({"tool_calls": [{"index": index, **call}]})
    yield frame({}, finish_reason)
    yield {**base, "choices": [], "usage": payload["usage"]}


@pytest.fixture
def stub_openai():
    """A minimal OpenAI-compatible endpoint recording what the client sends."""
    state = {"responses": [], "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["requests"].append(body)
            queued = state["responses"]
            payload = queued.pop(0) if queued else text_completion("")
            if body.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for chunk in _as_chunks(payload):
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")
                return
            encoded = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state["url"] = f"http://127.0.0.1:{server.server_port}/v1"
    yield state
    server.shutdown()
    server.server_close()


class TestModelProtocol:
    @pytest.fixture(autouse=True)
    def _wire_stub(self, stub_openai, tmp_path, monkeypatch):
        monkeypatch.setattr(paths_module, "WORKSPACE_ROOT", tmp_path)
        llm = ChatOpenAI(
            api_key="stub-key",
            base_url=stub_openai["url"],
            model="stub-model",
            temperature=0,
        )
        monkeypatch.setattr(nodes_module, "llm", llm.bind_tools(tools))
        self.stub = stub_openai

    def test_text_reply_round_trips_over_http(self):
        self.stub["responses"].append(text_completion("hello from the stub"))
        events = drain(Session().send("hi"))

        text = "".join(e.text for e in of_type(events, AssistantToken))
        assert text == "hello from the stub"
        assert of_type(events, RunFinished)[-1].reason == "completed"

    def test_token_usage_comes_off_the_wire(self):
        self.stub["responses"].append(text_completion("counted", total_tokens=42))
        events = drain(Session().send("hi"))
        assert of_type(events, RunFinished)[-1].stats.total_tokens_used == 42

    def test_tool_call_is_parsed_into_an_approval_request(self):
        self.stub["responses"].append(
            tool_call_completion(
                "edit_file",
                {"filename": "hello.py", "find_str": "", "replace_str": "print('hi')"},
            )
        )
        session = Session()
        events = drain(session.send("write hello"))

        requested = of_type(events, ApprovalRequested)[0].requests[0]
        assert requested.tool == "edit_file"
        assert requested.args["filename"] == "hello.py"
        assert session.is_paused()

    def test_config_is_not_advertised_to_the_model(self):
        """The wire-level guard against RunnableConfig leaking into the schema.

        get_type_hints() rewrites `config: RunnableConfig = None` to Optional on
        Python <= 3.10, which stops LangChain recognising the parameter and
        publishes it as a real tool argument.
        """
        self.stub["responses"].append(text_completion("ok"))
        drain(Session().send("hi"))

        sent = {t["function"]["name"]: t["function"] for t in self.stub["requests"][0]["tools"]}
        assert set(sent) == {t.name for t in tools}
        for name, function in sent.items():
            assert "config" not in function["parameters"]["properties"], name
