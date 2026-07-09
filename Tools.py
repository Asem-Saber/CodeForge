import os
import ast
import importlib.util
import json
import logging
import pathlib
from langchain.tools import tool
from e2b_code_interpreter import Sandbox

logger = logging.getLogger("codeforge")

WORKSPACE = pathlib.Path("./workspace").resolve()
WORKSPACE.mkdir(exist_ok=True)

SANDBOX_TIMEOUT = 60

_sandbox_instance: Sandbox | None = None


def get_sandbox() -> Sandbox:
    global _sandbox_instance
    if _sandbox_instance is None:
        _sandbox_instance = Sandbox.create(timeout=300)
    return _sandbox_instance


def close_sandbox():
    global _sandbox_instance
    if _sandbox_instance is not None:
        _sandbox_instance.kill()
        _sandbox_instance = None


def ask_user_approval(message: str) -> bool:
    user_approval = input(f"{message} (y/n): ")
    return user_approval.lower() == "y"


def safe_path(filename: str) -> pathlib.Path:
    """Resolve a filename and ensure it stays within WORKSPACE."""
    target = (WORKSPACE / filename).resolve()
    if not target.is_relative_to(WORKSPACE):
        raise ValueError(f"Path escapes workspace: {filename}")
    return target


def _check_imports_raw(code: str) -> list[str]:
    """Return list of import issues (empty if all OK)."""
    tree = ast.parse(code)
    issues = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name.split(".")[0]
                if not importlib.util.find_spec(module):
                    issues.append(f"Module '{alias.name}' not found")
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                module = node.module.split(".")[0]
                if not importlib.util.find_spec(module):
                    issues.append(f"Module '{node.module}' not found")
    return issues


@tool
def edit_file(filename: str, find_str: str, replace_str: str) -> str:
    """Apply a diff to a file by replacing occurrences of find_str with replace_str. If find_str is empty and file doesn't exist, it creates the file. All paths are relative to the workspace directory."""
    logger.info("Editing file: %s", filename)
    try:
        target = safe_path(filename)
    except ValueError as e:
        return f"Error: {e}"

    if find_str != "":
        logger.debug("Content to find: %s", find_str[:100])
    if replace_str != "":
        logger.debug("Content to replace with: %s", replace_str[:100])

    if not ask_user_approval(f"Do you want to edit file '{filename}'?"):
        logger.info("File edit cancelled by user.")
        return "File edit cancelled by user."

    if not target.exists() and find_str == "":
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(replace_str)
        return "Success: File created."

    try:
        content = target.read_text()

        if find_str in content:
            new_content = content.replace(find_str, replace_str)
            target.write_text(new_content)
            return "Success: File edited."
        else:
            return "Error: find_str not found in file."
    except FileNotFoundError:
        return f"Error: File {filename} not found and find_str is not empty."
    except Exception as e:
        return f"Error editing file: {str(e)}"


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


@tool
def list_directory(path: str = ".") -> str:
    """List the contents of a directory within the workspace."""
    logger.info("Listing directory: %s", path)
    try:
        target = safe_path(path)
    except ValueError as e:
        return f"Error: {e}"
    try:
        items = os.listdir(target)
        if not items:
            return f"Directory '{path}' is empty."
        result = f"Contents of directory '{path}':\n"
        for item in items:
            full_path = target / item
            item_type = "Directory" if full_path.is_dir() else "File"
            result += f"- {item} ({item_type})\n"
        return result.strip()
    except FileNotFoundError:
        return f"Error: Directory '{path}' not found."
    except PermissionError:
        return f"Error: Permission denied to access '{path}'."
    except Exception as e:
        return f"Error listing directory '{path}': {str(e)}"


@tool
def read_file_content(path: str) -> str:
    """Read and return the content of a file within the workspace."""
    logger.info("Reading file: %s", path)
    try:
        target = safe_path(path)
    except ValueError as e:
        return f"Error: {e}"
    try:
        content = target.read_text(encoding="utf-8")
        if len(content) > 2000:
            content = content[:1000] + "\n\n[...content clipped...]\n\n" + content[-1000:]
        return content
    except FileNotFoundError:
        return f"Error: File '{path}' not found."
    except PermissionError:
        return f"Error: Permission denied to access '{path}'."
    except UnicodeDecodeError:
        return f"Error: Unable to decode '{path}'."
    except Exception as e:
        return f"Error reading file '{path}': {str(e)}"


@tool
def validate_python_syntax(code: str) -> str:
    """Validate that generated Python code is syntactically correct."""
    try:
        ast.parse(code)
        return "Syntax is valid."
    except SyntaxError as e:
        return f"Syntax error at line {e.lineno}: {e.msg}\n{e.text}"


@tool
def validate_imports(code: str) -> str:
    """Check that all imports in the generated code resolve to installed or local modules."""
    issues = _check_imports_raw(code)
    if issues:
        return "Import issues:\n" + "\n".join(f"  - {i}" for i in issues)
    return "All imports are valid."


tools = [edit_file, run_sandboxed_code, list_directory, read_file_content, validate_python_syntax, validate_imports]
