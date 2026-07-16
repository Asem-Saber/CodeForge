import json
import logging
from langchain.tools import tool
from src.config import SANDBOX_TIMEOUT
from src.sandbox.paths import safe_path
from src.sandbox.manager import get_sandbox, ask_user_approval

logger = logging.getLogger("codeforge")


@tool
def run_sandboxed_code(filename: str, language: str = "python") -> str:
    """Run code from a local file in an isolated E2B cloud sandbox.
    The file must exist in the workspace (saved via edit_file first).
    Returns a JSON object with stdout, stderr, exit_code, error, and artifacts.
    Supported languages: python, javascript, typescript, r, java."""
    try:
        target = safe_path(filename)
        code = target.read_text(encoding="utf-8")
    except ValueError as e:
        return json.dumps({"stdout": "", "stderr": str(e), "exit_code": 1, "error": "PathError", "artifacts": []})
    except FileNotFoundError:
        return json.dumps({"stdout": "", "stderr": f"File '{filename}' not found. Save the code with edit_file first.", "exit_code": 1, "error": "FileNotFoundError", "artifacts": []})
    except Exception as e:
        return json.dumps({"stdout": "", "stderr": f"Error reading file: {str(e)}", "exit_code": 1, "error": type(e).__name__, "artifacts": []})

    logger.info("Running %s code from %s in sandbox", language, filename)
    if not ask_user_approval(f"Do you want to run code from '{filename}' in the sandbox?"):
        logger.info("Sandbox execution cancelled by user.")
        return json.dumps({"stdout": "", "stderr": "Execution cancelled by user.", "exit_code": -1, "error": None, "artifacts": []})

    try:
        sbx = get_sandbox()
        execution = sbx.run_code(code, language=language, timeout=SANDBOX_TIMEOUT)

        stdout = ""
        stderr = ""
        if execution.logs.stdout:
            stdout = "".join(execution.logs.stdout)
            if len(stdout) > 2000:
                stdout = stdout[:1000] + "\n\n[...clipped...]\n\n" + stdout[-1000:]
        if execution.logs.stderr:
            stderr = "".join(execution.logs.stderr)
            if len(stderr) > 2000:
                stderr = stderr[:1000] + "\n\n[...clipped...]\n\n" + stderr[-1000:]

        error_detail = None
        if execution.error:
            error_detail = f"{execution.error.name}: {execution.error.value}\n{execution.error.traceback}"

        artifacts = []
        if execution.results:
            for r in execution.results:
                if r.png:
                    artifacts.append("[PNG image generated]")
                elif r.text:
                    artifacts.append(f"Result: {r.text}")

        exit_code = 1 if execution.error else 0
        result = {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "error": error_detail,
            "artifacts": artifacts,
        }
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"stdout": "", "stderr": f"Sandbox execution failed: {str(e)}", "exit_code": 1, "error": "SandboxError", "artifacts": []})
