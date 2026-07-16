import pathlib

WORKSPACE = pathlib.Path("./workspace").resolve()
WORKSPACE.mkdir(exist_ok=True)


def safe_path(filename: str) -> pathlib.Path:
    """Resolve a filename and ensure it stays within WORKSPACE."""
    target = (WORKSPACE / filename).resolve()
    if not target.is_relative_to(WORKSPACE):
        raise ValueError(f"Path escapes workspace: {filename}")
    return target
