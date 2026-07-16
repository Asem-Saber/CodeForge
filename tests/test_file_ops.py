import pytest
import src.sandbox.paths as paths_module
from src.tools.file_ops import edit_file, read_file_content, list_directory


@pytest.fixture(autouse=True)
def use_tmp_workspace(tmp_path, monkeypatch):
    """Redirect WORKSPACE to a temp directory for every test."""
    monkeypatch.setattr(paths_module, "WORKSPACE", tmp_path)


@pytest.fixture(autouse=True)
def auto_approve(monkeypatch):
    """Skip user approval prompts in tests."""
    import src.tools.file_ops as file_ops_module
    monkeypatch.setattr(file_ops_module, "ask_user_approval", lambda msg: True)


class TestEditFile:
    def test_create_new_file(self, tmp_path):
        result = edit_file.invoke({"filename": "hello.py", "find_str": "", "replace_str": "print('hello')"})
        assert result == "Success: File created."
        assert (tmp_path / "hello.py").read_text() == "print('hello')"

    def test_edit_existing_file(self, tmp_path):
        (tmp_path / "app.py").write_text("x = 1")
        result = edit_file.invoke({"filename": "app.py", "find_str": "x = 1", "replace_str": "x = 2"})
        assert result == "Success: File edited."
        assert (tmp_path / "app.py").read_text() == "x = 2"

    def test_find_str_not_found(self, tmp_path):
        (tmp_path / "app.py").write_text("x = 1")
        result = edit_file.invoke({"filename": "app.py", "find_str": "y = 99", "replace_str": "z = 0"})
        assert "find_str not found" in result

    def test_create_nested_file(self, tmp_path):
        result = edit_file.invoke({"filename": "sub/dir/test.py", "find_str": "", "replace_str": "pass"})
        assert result == "Success: File created."
        assert (tmp_path / "sub" / "dir" / "test.py").read_text() == "pass"

    def test_rejects_path_escape(self):
        result = edit_file.invoke({"filename": "../../evil.py", "find_str": "", "replace_str": "bad"})
        assert "Error" in result
        assert "escapes workspace" in result

    def test_user_cancels(self, monkeypatch):
        import src.tools.file_ops as m
        monkeypatch.setattr(m, "ask_user_approval", lambda msg: False)
        result = edit_file.invoke({"filename": "nope.py", "find_str": "", "replace_str": "x"})
        assert "cancelled" in result


class TestReadFileContent:
    def test_read_existing_file(self, tmp_path):
        (tmp_path / "data.txt").write_text("hello world")
        result = read_file_content.invoke({"path": "data.txt"})
        assert result == "hello world"

    def test_read_nonexistent_file(self):
        result = read_file_content.invoke({"path": "missing.txt"})
        assert "not found" in result

    def test_clips_large_file(self, tmp_path):
        (tmp_path / "big.txt").write_text("A" * 5000)
        result = read_file_content.invoke({"path": "big.txt"})
        assert "[...content clipped...]" in result

    def test_rejects_path_escape(self):
        result = read_file_content.invoke({"path": "../../etc/passwd"})
        assert "Error" in result


class TestListDirectory:
    def test_list_empty_dir(self, tmp_path):
        (tmp_path / "empty").mkdir()
        result = list_directory.invoke({"path": "empty"})
        assert "empty" in result.lower()

    def test_list_with_files(self, tmp_path):
        (tmp_path / "a.py").write_text("")
        (tmp_path / "b.txt").write_text("")
        result = list_directory.invoke({"path": "."})
        assert "a.py" in result
        assert "b.txt" in result

    def test_shows_file_types(self, tmp_path):
        (tmp_path / "script.py").write_text("")
        (tmp_path / "subdir").mkdir()
        result = list_directory.invoke({"path": "."})
        assert "File" in result
        assert "Directory" in result

    def test_rejects_path_escape(self):
        result = list_directory.invoke({"path": "../.."})
        assert "Error" in result
