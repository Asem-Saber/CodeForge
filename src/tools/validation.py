import ast
import importlib.util
from langchain.tools import tool


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
