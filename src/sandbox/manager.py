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
