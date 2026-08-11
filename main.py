import argparse

from src.config import logger
from src.agent.graph import loop, new_session_id, list_sessions, session_exists
from src.sandbox.paths import WORKSPACE_ROOT, normalize_session_id
from src.sandbox.manager import close_all_sandboxes


def describe_session(session_id: str) -> str:
    workspace = WORKSPACE_ROOT / session_id
    if not workspace.is_dir():
        return "no workspace"
    files = sorted(p.name for p in workspace.iterdir() if p.is_file())
    if not files:
        return "no files"
    shown = ", ".join(files[:4])
    return shown + (f", +{len(files) - 4} more" if len(files) > 4 else "")


def print_sessions():
    sessions = list_sessions()
    if not sessions:
        print("No saved sessions yet.")
        return
    print("Saved sessions:")
    for session_id in sessions:
        print(f"  {session_id}  ({describe_session(session_id)})")
    print("\nResume one with: python main.py --session <id>")


def parse_args():
    parser = argparse.ArgumentParser(description="CodeForge coding agent")
    parser.add_argument("-s", "--session", help="resume a previous session by id")
    parser.add_argument("-l", "--list", action="store_true", help="list saved sessions and exit")
    return parser.parse_args()


def main():
    args = parse_args()

    if args.list:
        print_sessions()
        return

    if args.session:
        try:
            session_id = normalize_session_id(args.session)
        except ValueError as e:
            raise SystemExit(f"Error: {e}")
        if session_exists(session_id):
            print(f"Resuming session: {session_id}  ({describe_session(session_id)})")
        else:
            print(f"No saved state for session {session_id} — starting it fresh.")
    else:
        session_id = new_session_id()
        print(f"New session: {session_id}")

    try:
        while True:
            try:
                user_input = input("How can I help you?\n")
                if user_input.lower() in ["exit", "quit"]:
                    break
                loop(user_input, session_id)
            except KeyboardInterrupt:
                break
            except EOFError:
                break
    finally:
        close_all_sandboxes()
        logger.info("Sandboxes closed.")
        print(f"\nResume this session with: python main.py --session {session_id}")


if __name__ == "__main__":
    main()
