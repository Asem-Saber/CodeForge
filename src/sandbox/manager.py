import atexit
import docker
import logging
from src.sandbox.paths import WORKSPACE

logger = logging.getLogger("codeforge")

_client: docker.DockerClient | None = None
_container = None

SANDBOX_IMAGE = "codeforge-sandbox"


def get_sandbox():
    global _client, _container
    if _container is None:
        _client = docker.from_env()
        _container = _client.containers.run(
            SANDBOX_IMAGE,
            detach=True,
            mem_limit="512m",
            cpu_period=100000,
            cpu_quota=50000,
            network_mode="bridge",
            security_opt=["no-new-privileges"],
            cap_drop=["ALL"],
            working_dir="/sandbox",
            volumes={str(WORKSPACE): {"bind": "/sandbox", "mode": "rw"}},
        )
        atexit.register(close_sandbox)
        logger.info("Docker sandbox started: %s", _container.short_id)
    return _container


def close_sandbox():
    global _container
    if _container is not None:
        try:
            _container.stop(timeout=5)
            _container.remove()
            logger.info("Docker sandbox stopped and removed.")
        except Exception:
            pass
        _container = None


def ask_user_approval(message: str) -> bool:
    user_approval = input(f"{message} (y/n): ")
    return user_approval.lower() == "y"
