import os
import atexit
import shutil
import tempfile

os.environ.setdefault("API_KEY", "test-key")
os.environ.setdefault("ENDPOINT", "http://localhost:1234")
os.environ.setdefault("MODEL_ID", "test-model")

# Keep the suite off the real checkpoint database. Must be set before anything
# imports src.agent.graph, which opens the connection at import time.
_CHECKPOINT_DIR = tempfile.mkdtemp(prefix="codeforge-tests-")
os.environ.setdefault("CHECKPOINT_DB", os.path.join(_CHECKPOINT_DIR, "checkpoints.sqlite"))
atexit.register(shutil.rmtree, _CHECKPOINT_DIR, True)
