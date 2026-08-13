"""End-to-end tests against a real Docker sandbox.

Everything here needs a reachable daemon and a built `codeforge-sandbox`
image; without either the whole module skips, so the default `pytest` run
stays offline. Build the image with `docker compose build sandbox-image`.
"""

import json
import uuid

import pytest
from docker.errors import NotFound

import src.sandbox.manager as manager
from src.sandbox.manager import (
    CONTAINER_PREFIX,
    SANDBOX_IMAGE,
    close_all_sandboxes,
    close_sandbox,
    get_sandbox,
)
from src.sandbox.paths import session_workspace
from src.tools.execution import run_sandboxed_code
from src.tools.file_ops import edit_file
from src.tools.validation import validate_imports, validate_python_syntax

pytestmark = pytest.mark.integration


@pytest.fixture
def session_id(docker_client, sandbox_workspace_root):
    """A throwaway session, torn down even when the test fails."""
    sid = f"itest-{uuid.uuid4().hex[:8]}"
    manager._containers.pop(sid, None)
    yield sid
    close_all_sandboxes()
    try:
        docker_client.containers.get(f"{CONTAINER_PREFIX}{sid}").remove(force=True)
    except Exception:
        pass


def config_for(session_id):
    return {"configurable": {"thread_id": session_id}}


class TestContainerLifecycle:
    def test_starts_a_running_container(self, session_id, docker_client):
        container = get_sandbox(session_id)
        container.reload()
        assert container.status == "running"
        assert container.name == f"{CONTAINER_PREFIX}{session_id}"

    def test_labels_the_container_with_its_session(self, session_id):
        container = get_sandbox(session_id)
        assert container.labels["codeforge.session"] == session_id

    def test_reuses_the_container_across_calls(self, session_id):
        first = get_sandbox(session_id)
        second = get_sandbox(session_id)
        assert first.id == second.id

    def test_separate_sessions_get_separate_containers(self, session_id, docker_client):
        other = f"{session_id}-b"
        try:
            assert get_sandbox(session_id).id != get_sandbox(other).id
        finally:
            close_sandbox(other)

    def test_close_sandbox_removes_the_container(self, session_id, docker_client):
        name = get_sandbox(session_id).name
        close_sandbox(session_id)
        with pytest.raises(NotFound):
            docker_client.containers.get(name)

    def test_recovers_from_a_stale_container(self, session_id, docker_client):
        """A leftover container under the same name is replaced, not fatal."""
        name = f"{CONTAINER_PREFIX}{session_id}"
        stale = docker_client.containers.run(SANDBOX_IMAGE, "sleep infinity", detach=True, name=name)
        try:
            container = get_sandbox(session_id)
            assert container.id != stale.id
        finally:
            try:
                stale.remove(force=True)
            except Exception:
                pass

    def test_applies_the_hardening_flags(self, session_id):
        host_config = get_sandbox(session_id).attrs["HostConfig"]
        assert host_config["CapDrop"] == ["ALL"]
        assert "no-new-privileges" in host_config["SecurityOpt"]
        assert host_config["Memory"] == 512 * 1024 * 1024


class TestWorkspaceMount:
    def test_workspace_is_visible_inside_the_container(self, session_id):
        (session_workspace(session_id) / "note.txt").write_text("from the host")
        exit_code, output = get_sandbox(session_id).exec_run("cat /sandbox/note.txt")
        assert exit_code == 0
        assert output.decode() == "from the host"

    def test_sessions_cannot_see_each_other(self, session_id):
        (session_workspace(session_id) / "mine.txt").write_text("private")
        other = f"{session_id}-b"
        try:
            exit_code, _ = get_sandbox(other).exec_run("cat /sandbox/mine.txt")
            assert exit_code != 0
        finally:
            close_sandbox(other)


class TestRunSandboxedCode:
    def test_runs_a_file_written_by_edit_file(self, session_id):
        config = config_for(session_id)
        edit_file.invoke(
            {"filename": "hello.py", "find_str": "", "replace_str": "print('hello from the sandbox')"},
            config=config,
        )
        result = json.loads(run_sandboxed_code.invoke({"filename": "hello.py"}, config=config))
        assert result["exit_code"] == 0
        assert "hello from the sandbox" in result["stdout"]
        assert result["error"] is None

    def test_reports_a_failing_script(self, session_id):
        config = config_for(session_id)
        edit_file.invoke(
            {"filename": "boom.py", "find_str": "", "replace_str": "raise ValueError('nope')"},
            config=config,
        )
        result = json.loads(run_sandboxed_code.invoke({"filename": "boom.py"}, config=config))
        assert result["exit_code"] != 0
        assert "ValueError" in result["stderr"]
        assert result["error"]

    def test_missing_file_is_reported_before_execution(self, session_id):
        result = json.loads(
            run_sandboxed_code.invoke({"filename": "absent.py"}, config=config_for(session_id))
        )
        assert result["error"] == "FileNotFoundError"

    def test_runs_against_the_sandbox_interpreter(self, session_id):
        """sklearn ships in the sandbox image and is absent from the host."""
        config = config_for(session_id)
        edit_file.invoke(
            {"filename": "probe.py", "find_str": "", "replace_str": "import sklearn, sys; print(sys.executable)"},
            config=config,
        )
        result = json.loads(run_sandboxed_code.invoke({"filename": "probe.py"}, config=config))
        assert result["exit_code"] == 0
        assert result["stdout"].strip().startswith("/usr/local/")


class TestValidationInSandbox:
    def test_accepts_valid_syntax(self, session_id):
        result = validate_python_syntax.invoke({"code": "x = 1\nprint(x)"}, config=config_for(session_id))
        assert result == "Syntax is valid."

    def test_reports_a_syntax_error(self, session_id):
        result = validate_python_syntax.invoke({"code": "def foo(\n"}, config=config_for(session_id))
        assert "Syntax error" in result

    def test_imports_are_checked_against_the_sandbox_not_the_host(self, session_id):
        """langchain is installed here but not in the sandbox image.

        _check_imports_raw falls back to a host check when the sandbox is
        unreachable, so this doubles as proof the check really ran in the
        container.
        """
        result = validate_imports.invoke({"code": "import langchain"}, config=config_for(session_id))
        assert "langchain" in result
        assert "not found" in result

    def test_accepts_preinstalled_sandbox_packages(self, session_id):
        result = validate_imports.invoke(
            {"code": "import numpy, pandas, sklearn"}, config=config_for(session_id)
        )
        assert result == "All imports are valid."
