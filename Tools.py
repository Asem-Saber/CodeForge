import os
import subprocess
from langchain.tools import tool 



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
def run_command(command: str, working_dir: str) -> str:
    """Run a shell command and return its output and error code."""
    print(f"Executing command: {command} in {working_dir}")
    if not ask_user_approval("Do you want to execute this command?"):
        print("Command execution cancelled by user.")
        return "Command execution cancelled by user."
    
    try:
        process = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=working_dir,
        )
        output, _ = process.communicate()
        if len(output) > 2000:
            output = output[:1000] + "\n\n[...content clipped...]\n\n" + output[-1000:]
        return f"Output:\n{output}\nReturn code: {process.returncode}"
    except Exception as e:
        return f"Error: {str(e)}"




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


tools = [edit_file, run_command, list_directory, read_file_content]