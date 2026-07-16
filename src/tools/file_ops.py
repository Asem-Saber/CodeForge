import os
import logging
from langchain.tools import tool
from src.sandbox.paths import safe_path
from src.sandbox.manager import ask_user_approval

logger = logging.getLogger("codeforge")


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
