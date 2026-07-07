import os
import ast
import importlib.util
from langchain.tools import tool
from e2b_code_interpreter import Sandbox

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

@tool
def edit_file(filename: str, find_str: str, replace_str: str) -> str:
    """Apply a diff to a file by replacing occurrences of find_str with replace_str. If find_str is empty and file doesn't exist, it creates the file."""
    print(f"Editing file: {filename}")
    if find_str != "":
        print(f"Content to find\n```\n{find_str}\n```")
    if replace_str != "":
        print(f"Content to replace with\n```\n{replace_str}\n```")
    
    if not ask_user_approval("Do you want to edit this file?"):
        print("File edit cancelled by user.")
        return "File edit cancelled by user."

    if not os.path.exists(filename) and find_str == "":
        with open(filename, "w") as f:
            f.write(replace_str)
        return "Success: File created."

    try:
        with open(filename, "r") as f:
            content = f.read()

        if find_str in content:
            new_content = content.replace(find_str, replace_str)
            with open(filename, "w") as f:
                f.write(new_content)
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
    The file must exist locally (saved via edit_file first).
    Returns stdout, stderr, exit code, and any artifacts.
    Supported languages: python, javascript, typescript, r, java."""
    try:
        with open(filename, "r", encoding="utf-8") as f:
            code = f.read()
    except FileNotFoundError:
        return f"Error: File '{filename}' not found. Save the code with edit_file first.\nEXIT_CODE: 1"
    except Exception as e:
        return f"Error reading file '{filename}': {str(e)}\nEXIT_CODE: 1"

    print(f"Running {language} code from {filename} in sandbox:\n```\n{code}\n```")
    if not ask_user_approval("Do you want to run this code in the sandbox?"):
        print("Sandbox execution cancelled by user.")
        return "Sandbox execution cancelled by user."

    try:
        sbx = get_sandbox()
        execution = sbx.run_code(code, language=language)

        result_parts = []
        if execution.logs.stdout:
            stdout = "".join(execution.logs.stdout)
            if len(stdout) > 2000:
                stdout = stdout[:1000] + "\n\n[...clipped...]\n\n" + stdout[-1000:]
            result_parts.append(f"STDOUT:\n{stdout}")
        if execution.logs.stderr:
            stderr = "".join(execution.logs.stderr)
            if len(stderr) > 2000:
                stderr = stderr[:1000] + "\n\n[...clipped...]\n\n" + stderr[-1000:]
            result_parts.append(f"STDERR:\n{stderr}")
        if execution.error:
            result_parts.append(f"ERROR: {execution.error.name}: {execution.error.value}\n{execution.error.traceback}")
        if execution.results:
            artifact_summaries = []
            for r in execution.results:
                if r.png:
                    artifact_summaries.append("[PNG image generated]")
                elif r.text:
                    artifact_summaries.append(f"Result: {r.text}")
            if artifact_summaries:
                result_parts.append("ARTIFACTS:\n" + "\n".join(artifact_summaries))

        exit_code = 1 if execution.error else 0
        result_parts.append(f"EXIT_CODE: {exit_code}")

        return "\n".join(result_parts) if result_parts else "Code executed successfully with no output.\nEXIT_CODE: 0"
    except Exception as e:
        return f"Sandbox execution failed: {str(e)}\nEXIT_CODE: 1"

@tool
def list_directory(path: str = ".") -> str:
    """List the contents of a directory."""
    print(f"Listing directory: {path}")
    try:
        items = os.listdir(path)
        if not items:
            return f"Directory '{path}' is empty."
        result = f"Contents of directory '{path}':\n"
        for item in items:
            full_path = os.path.join(path, item)
            item_type = "Directory" if os.path.isdir(full_path) else "File"
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
    """Read and return the content of a file."""
    print(f"Reading file: {path}")
    try:
        with open(path, "r", encoding="utf-8") as file:
            content = file.read()
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
    if issues:
        return "Import issues:\n" + "\n".join(f"  - {i}" for i in issues)
    return "All imports are valid."

tools = [edit_file, run_sandboxed_code, list_directory, read_file_content, validate_python_syntax, validate_imports]