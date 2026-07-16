import pathlib
import pytest
from src.sandbox.paths import safe_path, WORKSPACE


class TestSafePath:
    def test_simple_filename(self):
        result = safe_path("test.py")
        assert result == WORKSPACE / "test.py"

    def test_nested_path(self):
        result = safe_path("subdir/test.py")
        assert result == WORKSPACE / "subdir" / "test.py"

    def test_blocks_parent_traversal(self):
        with pytest.raises(ValueError, match="Path escapes workspace"):
            safe_path("../../etc/passwd")

    def test_blocks_absolute_path_outside(self):
        with pytest.raises(ValueError, match="Path escapes workspace"):
            safe_path("/etc/passwd")

    def test_blocks_double_dot_in_middle(self):
        with pytest.raises(ValueError, match="Path escapes workspace"):
            safe_path("subdir/../../outside.py")

    def test_current_dir_resolves_to_workspace(self):
        result = safe_path(".")
        assert result == WORKSPACE

    def test_workspace_is_absolute(self):
        assert WORKSPACE.is_absolute()

    def test_workspace_exists(self):
        assert WORKSPACE.exists()
