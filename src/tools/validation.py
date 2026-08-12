import ast
import json
import logging
from langchain.tools import tool
from langchain_core.runnables import RunnableConfig
from src.sandbox.paths import session_id_from_config

logger = logging.getLogger("codeforge")


def _run_in_sandbox(python_code: str, session_id: str | None = None) -> tuple[int, str, str]:
    """Run a Python snippet inside the Docker sandbox. Returns (exit_code, stdout, stderr)."""
    from src.sandbox.manager import get_sandbox

    container = get_sandbox(session_id)
    exit_code, output = container.exec_run(
        ["python", "-c", python_code],
        demux=True,
    )
    stdout = output[0].decode("utf-8", errors="replace") if output[0] else ""
    stderr = output[1].decode("utf-8", errors="replace") if output[1] else ""
    return exit_code, stdout, stderr


def _check_syntax_in_sandbox(code: str, session_id: str | None = None) -> str:
    """Validate Python syntax inside the Docker sandbox."""
    check_code = (
        "import ast, json, sys\n"
        "try:\n"
        f"    ast.parse({repr(code)})\n"
        '    print(json.dumps({"valid": True}))\n'
        "except SyntaxError as e:\n"
        '    print(json.dumps({"valid": False, "lineno": e.lineno, "msg": e.msg, "text": e.text}))\n'
    )
    exit_code, stdout, stderr = _run_in_sandbox(check_code, session_id)
    if exit_code == 0 and stdout.strip():
        result = json.loads(stdout.strip())
        if result["valid"]:
            return "Syntax is valid."
        text = result.get("text") or ""
        return f"Syntax error at line {result['lineno']}: {result['msg']}\n{text}"
    return f"Sandbox syntax check failed: {stderr}"


def _check_imports_in_sandbox(code: str, session_id: str | None = None) -> list[str]:
    """Check if imports in code resolve to modules available in the Docker sandbox."""
    modules = _extract_imports(code)
    if not modules:
        return []

    check_code = (
        "import importlib.util, json\n"
        f"modules = {repr(modules)}\n"
        "issues = []\n"
        "for m in modules:\n"
        "    top = m.split('.')[0]\n"
        "    if not importlib.util.find_spec(top):\n"
        "        issues.append(f\"Module '{m}' not found\")\n"
        "print(json.dumps(issues))\n"
    )
    exit_code, stdout, stderr = _run_in_sandbox(check_code, session_id)
    if exit_code == 0 and stdout.strip():
        return json.loads(stdout.strip())
    return []


def _extract_imports(code: str) -> list[str]:
    """Extract module names from import statements."""
    tree = ast.parse(code)
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.append(node.module)
    return modules


def _check_imports_raw(code: str, session_id: str | None = None) -> list[str]:
    """Check imports against the Docker sandbox environment.
    Falls back to host-based check if Docker is unavailable (e.g. in tests)."""
    modules = _extract_imports(code)
    if not modules:
        return []

    try:
        return _check_imports_in_sandbox(code, session_id)
    except Exception as e:
        logger.debug("Sandbox import check unavailable (%s), falling back to host", e)
        import importlib.util
        issues = []
        for m in modules:
            top = m.split(".")[0]
            if not importlib.util.find_spec(top):
                issues.append(f"Module '{m}' not found")
        return issues


@tool
def validate_python_syntax(code: str, config: RunnableConfig) -> str:
    """Validate that generated Python code is syntactically correct.
    Runs the check inside the Docker sandbox to match the execution environment."""
    try:
        return _check_syntax_in_sandbox(code, session_id_from_config(config))
    except Exception as e:
        logger.debug("Sandbox syntax check unavailable (%s), falling back to host", e)
        try:
            ast.parse(code)
            return "Syntax is valid."
        except SyntaxError as e:
            return f"Syntax error at line {e.lineno}: {e.msg}\n{e.text}"


@tool
def validate_imports(code: str, config: RunnableConfig) -> str:
    """Check that all imports in the code resolve to modules available in the sandbox.
    Runs the check inside the Docker sandbox to match the execution environment."""
    issues = _check_imports_raw(code, session_id_from_config(config))
    if issues:
        return "Import issues:\n" + "\n".join(f"  - {i}" for i in issues)
    return "All imports are valid."
