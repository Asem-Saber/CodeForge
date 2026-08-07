import json
import logging
from langchain.tools import tool
from src.sandbox.manager import get_sandbox

logger = logging.getLogger("codeforge")


@tool
def install_package(package_name: str) -> str:
    """Install a Python package in the sandbox environment.
    Call this before running code that requires a package not in the base image.
    Pre-installed: numpy, pandas, matplotlib, requests, beautifulsoup4,
    scipy, scikit-learn, pillow, tabulate, fastapi, httpx."""
    logger.info("Installing package: %s", package_name)

    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.[]=<>,")
    if not package_name or not all(c in allowed for c in package_name):
        return json.dumps({"success": False, "error": f"Invalid package name: {package_name}"})

    try:
        container = get_sandbox()
        exit_code, output = container.exec_run(
            f"pip install --user --no-cache-dir {package_name}",
            demux=True,
        )

        stdout = output[0].decode("utf-8", errors="replace") if output[0] else ""
        stderr = output[1].decode("utf-8", errors="replace") if output[1] else ""

        if exit_code == 0:
            logger.info("Installed %s successfully", package_name)
            return json.dumps({"success": True, "output": stdout[-500:]})
        else:
            logger.warning("Failed to install %s", package_name)
            return json.dumps({"success": False, "error": (stderr or stdout)[-500:]})
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})
