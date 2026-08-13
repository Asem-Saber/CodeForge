import os
import atexit
import pathlib
import shutil
import stat
import tempfile

os.environ.setdefault("API_KEY", "test-key")
os.environ.setdefault("ENDPOINT", "http://localhost:1234")
os.environ.setdefault("MODEL_ID", "test-model")

_CHECKPOINT_DIR = tempfile.mkdtemp(prefix="codeforge-tests-")
os.environ.setdefault("CHECKPOINT_DB", os.path.join(_CHECKPOINT_DIR, "checkpoints.sqlite"))
atexit.register(shutil.rmtree, _CHECKPOINT_DIR, True)

import pytest 

import src.sandbox.paths as paths_module  
from src.sandbox.manager import SANDBOX_IMAGE, close_all_sandboxes  


@pytest.fixture(scope="session")
def docker_client():
    """A reachable daemon holding the sandbox image, or skip the test."""
    docker = pytest.importorskip("docker")
    try:
        client = docker.from_env()
        client.ping()
    except Exception as e:
        pytest.skip(f"Docker daemon unreachable: {e}")
    try:
        client.images.get(SANDBOX_IMAGE)
    except Exception:
        pytest.skip(f"Image {SANDBOX_IMAGE!r} not built")
    return client


@pytest.fixture
def sandbox_workspace_root(monkeypatch):
    """A workspace root the sandbox user can traverse.

    pytest's tmp_path sits under a 0700 base directory, so the container's
    non-root user cannot reach a bind mount pointing into it.
    """
    root = pathlib.Path(tempfile.mkdtemp(prefix="codeforge-sandbox-"))
    root.chmod(stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
    monkeypatch.setattr(paths_module, "WORKSPACE_ROOT", root)
    monkeypatch.delenv("HOST_WORKSPACE_ROOT", raising=False)
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def closes_sandboxes():
    """Tear down every container this process started, pass or fail.

    Only touches manager-tracked containers, so a developer's real session
    running alongside the suite is left alone.
    """
    yield
    close_all_sandboxes()
