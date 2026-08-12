import atexit
import docker
import logging
from docker.errors import APIError, NotFound
from src.sandbox.paths import normalize_session_id, sandbox_bind_source

logger = logging.getLogger("codeforge")

_client: docker.DockerClient | None = None
_containers: dict = {}
_atexit_registered = False

SANDBOX_IMAGE = "codeforge-sandbox"
CONTAINER_PREFIX = "codeforge-sbx-"


def _get_client() -> docker.DockerClient:
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client


def _container_name(session_id: str) -> str:
    return f"{CONTAINER_PREFIX}{session_id}"


def _start_container(session_id: str):
    client = _get_client()
    name = _container_name(session_id)
    kwargs = dict(
        detach=True,
        name=name,
        mem_limit="512m",
        cpu_period=100000,
        cpu_quota=50000,
        network_mode="bridge",
        security_opt=["no-new-privileges"],
        cap_drop=["ALL"],
        working_dir="/sandbox",
        volumes={sandbox_bind_source(session_id): {"bind": "/sandbox", "mode": "rw"}},
        labels={"codeforge.session": session_id},
    )
    try:
        return client.containers.run(SANDBOX_IMAGE, **kwargs)
    except APIError as e:
        if e.response is None or e.response.status_code != 409:
            raise
        logger.warning("Removing stale sandbox container %s", name)
        try:
            client.containers.get(name).remove(force=True)
        except NotFound:
            pass
        return client.containers.run(SANDBOX_IMAGE, **kwargs)


def get_sandbox(session_id: str | None = None):
    """Return the Docker sandbox owned by a session, starting it on first use."""
    global _atexit_registered
    sid = normalize_session_id(session_id)

    container = _containers.get(sid)
    if container is not None:
        return container

    container = _start_container(sid)
    _containers[sid] = container

    if not _atexit_registered:
        atexit.register(close_all_sandboxes)
        _atexit_registered = True

    logger.info("Docker sandbox started for session %s: %s", sid, container.short_id)
    return container


def close_sandbox(session_id: str | None = None):
    """Stop and remove one session's sandbox."""
    sid = normalize_session_id(session_id)
    container = _containers.pop(sid, None)
    if container is None:
        return
    try:
        container.stop(timeout=5)
        container.remove()
        logger.info("Docker sandbox for session %s stopped and removed.", sid)
    except Exception:
        logger.debug("Failed to clean up sandbox for session %s", sid, exc_info=True)


def close_all_sandboxes():
    """Stop and remove every sandbox this process started."""
    for sid in list(_containers):
        close_sandbox(sid)
