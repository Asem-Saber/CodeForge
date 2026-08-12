import os
import logging
import pathlib
from dotenv import load_dotenv

load_dotenv()

for _name in ("API_KEY", "LANGSMITH_API_KEY"):
    _path = os.environ.get(f"{_name}_FILE")
    if _path:
        _value = pathlib.Path(_path).read_text(encoding="utf-8").strip()
        if _value:
            os.environ[_name] = _value

API_KEY = os.environ["API_KEY"]
ENDPOINT = os.environ["ENDPOINT"]
MODEL_ID = os.environ["MODEL_ID"]

MAX_TURNS = 20
MAX_RETRIES = 3
TOKEN_BUDGET = 100_000
SANDBOX_TIMEOUT = 60

CHECKPOINT_DB = os.environ.get("CHECKPOINT_DB", "checkpoints.sqlite")

pathlib.Path("logs").mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    filename="logs/codeforge.log",
)
logger = logging.getLogger("codeforge")
