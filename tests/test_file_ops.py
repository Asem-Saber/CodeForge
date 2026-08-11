import pytest
import src.sandbox.paths as paths_module
from src.sandbox.paths import session_workspace
from src.tools.file_ops import edit_file, read_file_content, list_directory

SESSION = "test-session"
CONFIG = {"configurable": {"thread_id": SESSION}}


@pytest.fixture(autouse=True)
def use_tmp_workspace(tmp_path, monkeypatch):
    """Redirect the workspace root to a temp directory for every test."""
    monkeypatch.setattr(paths_module, "WORKSPACE_ROOT", tmp_path)


@pytest.fixture
def workspace():
    """The session's own directory under the temp workspace root."""
    return session_workspace(SESSION)


class TestEditFile:
    def test_create_new_file(self, workspace):
        result = edit_file.invoke({"filename": "hello.py", "find_str": "", "replace_str": "print('hello')"}, config=CONFIG)
        assert result == "Success: File created."
        assert (workspace / "hello.py").read_text() == "print('hello')"

    def test_edit_existing_file(self, workspace):
        (workspace / "app.py").write_text("x = 1")
        result = edit_file.invoke({"filename": "app.py", "find_str": "x = 1", "replace_str": "x = 2"}, config=CONFIG)
        assert result == "Success: File edited."
        assert (workspace / "app.py").read_text() == "x = 2"

    def test_find_str_not_found(self, workspace):
        (workspace / "app.py").write_text("x = 1")
        result = edit_file.invoke({"filename": "app.py", "find_str": "y = 99", "replace_str": "z = 0"}, config=CONFIG)
        assert "find_str not found" in result

    def test_create_nested_file(self, workspace):
        result = edit_file.invoke({"filename": "sub/dir/test.py", "find_str": "", "replace_str": "pass"}, config=CONFIG)
        assert result == "Success: File created."
        assert (workspace / "sub" / "dir" / "test.py").read_text() == "pass"

    def test_rejects_path_escape(self):
        result = edit_file.invoke({"filename": "../../evil.py", "find_str": "", "replace_str": "bad"}, config=CONFIG)
        assert "Error" in result
        assert "escapes workspace" in result

    def test_writes_into_the_calling_session(self, tmp_path):
        edit_file.invoke(
            {"filename": "mine.py", "find_str": "", "replace_str": "x = 1"},
            config={"configurable": {"thread_id": "session-one"}},
        )
        assert (tmp_path / "session-one" / "mine.py").exists()
        assert not (tmp_path / "session-two" / "mine.py").exists()


class TestReadFileContent:
    def test_read_existing_file(self, workspace):
        (workspace / "data.txt").write_text("hello world")
        result = read_file_content.invoke({"path": "data.txt"}, config=CONFIG)
        assert result == "hello world"

    def test_read_nonexistent_file(self):
        result = read_file_content.invoke({"path": "missing.txt"}, config=CONFIG)
        assert "not found" in result

    def test_clips_large_file(self, workspace):
        (workspace / "big.txt").write_text("A" * 5000)
        result = read_file_content.invoke({"path": "big.txt"}, config=CONFIG)
        assert "[...content clipped...]" in result

    def test_rejects_path_escape(self):
        result = read_file_content.invoke({"path": "../../etc/passwd"}, config=CONFIG)
        assert "Error" in result

    def test_cannot_read_another_session(self, tmp_path):
        other = session_workspace("session-other")
        (other / "secret.txt").write_text("classified")
        result = read_file_content.invoke({"path": "../session-other/secret.txt"}, config=CONFIG)
        assert "Error" in result
        assert "classified" not in result


class TestListDirectory:
    def test_list_empty_dir(self, workspace):
        (workspace / "empty").mkdir()
        result = list_directory.invoke({"path": "empty"}, config=CONFIG)
        assert "empty" in result.lower()

    def test_list_with_files(self, workspace):
        (workspace / "a.py").write_text("")
        (workspace / "b.txt").write_text("")
        result = list_directory.invoke({"path": "."}, config=CONFIG)
        assert "a.py" in result
        assert "b.txt" in result

    def test_shows_file_types(self, workspace):
        (workspace / "script.py").write_text("")
        (workspace / "subdir").mkdir()
        result = list_directory.invoke({"path": "."}, config=CONFIG)
        assert "File" in result
        assert "Directory" in result

    def test_rejects_path_escape(self):
        result = list_directory.invoke({"path": "../.."}, config=CONFIG)
        assert "Error" in result
