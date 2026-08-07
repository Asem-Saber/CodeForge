import json
import logging
from langchain.tools import tool
from src.config import SANDBOX_TIMEOUT
from src.sandbox.paths import safe_path
from src.sandbox.manager import get_sandbox, ask_user_approval

logger = logging.getLogger("codeforge")


@tool
def run_sandboxed_code(filename: str, language: str = "python") -> str:
    """Run code from a workspace file in an isolated Docker sandbox container.
    The file must exist in the workspace (saved via edit_file first).
    Returns a JSON object with stdout, stderr, exit_code, error, and artifacts."""
    try:
        target = safe_path(filename)
        if not target.exists():
            return json.dumps({"stdout": "", "stderr": f"File '{filename}' not found. Save the code with edit_file first.", "exit_code": 1, "error": "FileNotFoundError", "artifacts": []})
    except ValueError as e:
        return json.dumps({"stdout": "", "stderr": str(e), "exit_code": 1, "error": "PathError", "artifacts": []})

    logger.info("Running %s code from %s in sandbox", language, filename)
    if not ask_user_approval(f"Do you want to run code from '{filename}' in the sandbox?"):
        logger.info("Sandbox execution cancelled by user.")
        return json.dumps({"stdout": "", "stderr": "Execution cancelled by user.", "exit_code": -1, "error": None, "artifacts": []})

    try:
        container = get_sandbox()
        container_path = "/sandbox/" + filename.replace("\\", "/")

        exit_code, output = container.exec_run(
            f"timeout {SANDBOX_TIMEOUT} python {container_path}",
            demux=True,
        )

        stdout = output[0].decode("utf-8", errors="replace") if output[0] else ""
        stderr = output[1].decode("utf-8", errors="replace") if output[1] else ""

        if len(stdout) > 2000:
            stdout = stdout[:1000] + "\n\n[...clipped...]\n\n" + stdout[-1000:]
        if len(stderr) > 2000:
            stderr = stderr[:1000] + "\n\n[...clipped...]\n\n" + stderr[-1000:]

        if exit_code == 124:
            stderr += f"\nExecution timed out after {SANDBOX_TIMEOUT} seconds."

        error_detail = stderr if exit_code != 0 else None

        result = {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "error": error_detail,
            "artifacts": [],
        }
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"stdout": "", "stderr": f"Sandbox execution failed: {str(e)}", "exit_code": 1, "error": "SandboxError", "artifacts": []})
