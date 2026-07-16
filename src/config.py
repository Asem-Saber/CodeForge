import os
import logging
import pathlib
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ["API_KEY"]
ENDPOINT = os.environ["ENDPOINT"]
MODEL_ID = os.environ["MODEL_ID"]

MAX_TURNS = 20
MAX_RETRIES = 3
TOKEN_BUDGET = 100_000
SANDBOX_TIMEOUT = 60

pathlib.Path("logs").mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    filename="logs/codeforge.log",
)
logger = logging.getLogger("codeforge")
