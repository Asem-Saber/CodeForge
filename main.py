import argparse
import pathlib

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory

from src.config import logger
from src.service import Session, list_sessions
from src.sandbox.paths import normalize_session_id
from src.ui import ConsoleUI, handle_command, is_command

HISTORY_PATH = pathlib.Path("logs/history")


class InputReader:
    """Reads user input, degrading to input() where prompt_toolkit can't run.

    prompt_toolkit needs a real console, so it raises when stdin/stdout is a
    pipe or a non-Windows terminal emulator on Windows.
    """

    def __init__(self):
        self._session = self._make_session()

    @staticmethod
    def _make_session():
        try:
            HISTORY_PATH.parent.mkdir(exist_ok=True)
            return PromptSession(history=FileHistory(str(HISTORY_PATH)))
        except Exception:
            logger.debug("prompt_toolkit unavailable; using plain input()", exc_info=True)
            return None

    def read(self, prompt: str = "> ") -> str:
        if self._session is None:
            return input(prompt)
        try:
            return self._session.prompt(prompt)
        except (KeyboardInterrupt, EOFError):
            raise
        except Exception:
            logger.debug("prompt_toolkit failed; using plain input()", exc_info=True)
            self._session = None
            return input(prompt)


def resolve_pending(session: Session, ui: ConsoleUI):
    """Finish an approval left hanging by an interrupted turn."""
    while session.is_paused():
        decision = ui.ask_approval(session.pending_approval(), session)
        drive(session.resume(decision), session, ui)


def drive(events, session: Session, ui: ConsoleUI):
    for event in events:
        ui.handle(event, session)


def run_turn(session: Session, ui: ConsoleUI, user_input: str):
    """Stream a turn, pausing for approval as many times as the run needs."""
    drive(session.send(user_input), session, ui)
    while session.is_paused():
        decision = ui.ask_approval(session.pending_approval(), session)
        drive(session.resume(decision), session, ui)


def open_session(args, ui: ConsoleUI) -> Session:
    if not args.session:
        return Session()
    session = Session(normalize_session_id(args.session))
    if not session.exists():
        ui.print_note(f"No saved state for {session.session_id} - starting it fresh.")
    return session


def parse_args():
    parser = argparse.ArgumentParser(description="CodeForge coding agent")
    parser.add_argument("-s", "--session", help="resume a previous session by id")
    parser.add_argument("-l", "--list", action="store_true", help="list saved sessions and exit")
    return parser.parse_args()


def main():
    args = parse_args()
    ui = ConsoleUI()

    if args.list:
        ui.print_sessions(list_sessions())
        return

    try:
        session = open_session(args, ui)
    except ValueError as e:
        raise SystemExit(f"Error: {e}")

    ui.banner(session, resumed=bool(args.session))
    reader = InputReader()

    try:
        while True:
            try:
                resolve_pending(session, ui)
                user_input = reader.read().strip()
            except KeyboardInterrupt:
                continue  # Ctrl+C clears the line; Ctrl+D exits
            except EOFError:
                break

            if not user_input:
                continue

            if user_input.lower() in ("exit", "quit"):
                break

            if is_command(user_input):
                result = handle_command(user_input, session, ui)
                if result.should_exit:
                    break
                if result.session is not None:
                    session = result.session
                continue

            try:
                run_turn(session, ui, user_input)
            except KeyboardInterrupt:
                ui.print_note("Interrupted.")
    finally:
        session.close()
        logger.info("Sandbox closed for session %s", session.session_id)
        ui.print_note(f"Resume with: python main.py --session {session.session_id}")


if __name__ == "__main__":
    main()
