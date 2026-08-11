import pytest
import src.sandbox.paths as paths_module
from src.sandbox.paths import (
    safe_path,
    session_workspace,
    normalize_session_id,
    session_id_from_config,
    DEFAULT_SESSION_ID,
)

SESSION = "session-a"


@pytest.fixture(autouse=True)
def tmp_workspace_root(tmp_path, monkeypatch):
    monkeypatch.setattr(paths_module, "WORKSPACE_ROOT", tmp_path)
    return tmp_path


class TestSafePath:
    def test_simple_filename(self, tmp_workspace_root):
        assert safe_path("test.py", SESSION) == tmp_workspace_root / SESSION / "test.py"

    def test_nested_path(self, tmp_workspace_root):
        assert safe_path("subdir/test.py", SESSION) == tmp_workspace_root / SESSION / "subdir" / "test.py"

    def test_blocks_parent_traversal(self):
        with pytest.raises(ValueError, match="Path escapes workspace"):
            safe_path("../../etc/passwd", SESSION)

    def test_blocks_absolute_path_outside(self):
        with pytest.raises(ValueError, match="Path escapes workspace"):
            safe_path("/etc/passwd", SESSION)

    def test_blocks_double_dot_in_middle(self):
        with pytest.raises(ValueError, match="Path escapes workspace"):
            safe_path("subdir/../../outside.py", SESSION)

    def test_blocks_escape_into_another_session(self):
        with pytest.raises(ValueError, match="Path escapes workspace"):
            safe_path("../session-b/secret.py", SESSION)

    def test_current_dir_resolves_to_session_workspace(self):
        assert safe_path(".", SESSION) == session_workspace(SESSION)

    def test_defaults_to_default_session(self, tmp_workspace_root):
        assert safe_path("x.py") == tmp_workspace_root / DEFAULT_SESSION_ID / "x.py"


class TestSessionWorkspace:
    def test_is_absolute(self):
        assert session_workspace(SESSION).is_absolute()

    def test_created_on_demand(self):
        assert session_workspace("fresh-session").exists()

    def test_sessions_are_isolated(self):
        assert session_workspace("session-a") != session_workspace("session-b")


class TestNormalizeSessionId:
    def test_passes_through_uuid_like_id(self):
        assert normalize_session_id("abc-123") == "abc-123"

    def test_falls_back_to_default(self):
        assert normalize_session_id(None) == DEFAULT_SESSION_ID

    @pytest.mark.parametrize("bad", ["..", ".", "a/b", "a\\b", "   "])
    def test_rejects_path_separators_and_dots(self, bad):
        with pytest.raises(ValueError, match="Invalid session id"):
            normalize_session_id(bad)


class TestSessionIdFromConfig:
    def test_reads_thread_id(self):
        assert session_id_from_config({"configurable": {"thread_id": "t1"}}) == "t1"

    def test_handles_missing_config(self):
        assert session_id_from_config(None) == DEFAULT_SESSION_ID

    def test_handles_missing_configurable(self):
        assert session_id_from_config({}) == DEFAULT_SESSION_ID
