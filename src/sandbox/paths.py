import pathlib

WORKSPACE_ROOT = pathlib.Path("./workspace").resolve()
WORKSPACE_ROOT.mkdir(exist_ok=True)

DEFAULT_SESSION_ID = "default"


def normalize_session_id(session_id: str | None) -> str:
    """Reduce a session id to a single safe directory/container name component."""
    sid = (session_id or DEFAULT_SESSION_ID).strip()
    if not sid or sid in (".", "..") or any(c in sid for c in "/\\"):
        raise ValueError(f"Invalid session id: {session_id!r}")
    return sid


def session_id_from_config(config: dict | None) -> str:
    """Pull the session id out of a LangGraph RunnableConfig."""
    configurable = (config or {}).get("configurable") or {}
    return normalize_session_id(configurable.get("thread_id"))


def session_workspace(session_id: str | None = None) -> pathlib.Path:
    """Return (creating if needed) the workspace directory owned by a session."""
    root = (WORKSPACE_ROOT / normalize_session_id(session_id)).resolve()
    if not root.is_relative_to(WORKSPACE_ROOT):
        raise ValueError(f"Session workspace escapes root: {session_id!r}")
    root.mkdir(parents=True, exist_ok=True)
    return root


def safe_path(filename: str, session_id: str | None = None) -> pathlib.Path:
    """Resolve a filename and ensure it stays within the session's workspace."""
    root = session_workspace(session_id)
    target = (root / filename).resolve()
    if not target.is_relative_to(root):
        raise ValueError(f"Path escapes workspace: {filename}")
    return target
