"""Slash commands for the REPL."""
from dataclasses import dataclass

from src.service import Session, list_sessions

COMMANDS = [
    ("/help", "show this list"),
    ("/sessions", "list saved sessions"),
    ("/files", "list files in this session's workspace"),
    ("/cost", "show turn and token usage"),
    ("/new", "start a fresh session"),
    ("/exit", "quit"),
]


@dataclass
class CommandResult:
    handled: bool = False
    should_exit: bool = False
    session: Session | None = None  # set when the command swapped sessions


def is_command(text: str) -> bool:
    return text.strip().startswith("/")


def handle_command(text: str, session: Session, ui) -> CommandResult:
    name, _, _rest = text.strip().partition(" ")
    name = name.lower()

    if name in ("/exit", "/quit"):
        return CommandResult(handled=True, should_exit=True)

    if name == "/help":
        ui.print_help(COMMANDS)
        return CommandResult(handled=True)

    if name == "/sessions":
        ui.print_sessions(list_sessions())
        return CommandResult(handled=True)

    if name == "/files":
        ui.print_files(session)
        return CommandResult(handled=True)

    if name == "/cost":
        ui.print_cost(session)
        return CommandResult(handled=True)

    if name == "/new":
        session.close()
        fresh = Session()
        ui.print_note(f"Started session {fresh.session_id}")
        return CommandResult(handled=True, session=fresh)

    ui.print_error(f"Unknown command: {name}. Try /help.")
    return CommandResult(handled=True)
