import uuid
import sqlite3
import logging
from langgraph.graph import START, END, StateGraph
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.prebuilt import ToolNode
from langgraph.types import Command

from src.config import CHECKPOINT_DB
from src.tools import tools
from src.agent.state import AgentState
from src.agent.nodes import (
    call_model,
    validation_gate,
    approval_gate,
    process_execution,
    retry_router,
    route_after_agent,
    route_after_validation,
    route_after_approval,
    route_after_retry,
)

logger = logging.getLogger("codeforge")

workflow = StateGraph(AgentState)

workflow.add_node("agent", call_model)
workflow.add_node("validation_gate", validation_gate)
workflow.add_node("approval_gate", approval_gate)
workflow.add_node("tools", ToolNode(tools))
workflow.add_node("process_execution", process_execution)
workflow.add_node("retry_router", retry_router)

workflow.add_edge(START, "agent")

workflow.add_conditional_edges("agent", route_after_agent, {
    "__end__": END,
    "validation_gate": "validation_gate",
})

workflow.add_conditional_edges("validation_gate", route_after_validation, {
    "agent": "agent",
    "approval_gate": "approval_gate",
})

workflow.add_conditional_edges("approval_gate", route_after_approval, {
    "agent": "agent",
    "tools": "tools",
})

workflow.add_edge("tools", "process_execution")
workflow.add_edge("process_execution", "retry_router")

workflow.add_conditional_edges("retry_router", route_after_retry, {
    "__end__": END,
    "agent": "agent",
})

# check_same_thread=False so a web server can resume a thread from a different
# worker thread than the one that created it.
_conn = sqlite3.connect(CHECKPOINT_DB, check_same_thread=False)
checkpointer = SqliteSaver(_conn)
app = workflow.compile(checkpointer=checkpointer)


def new_session_id() -> str:
    return str(uuid.uuid4())


def list_sessions() -> list[str]:
    """Every session id the checkpointer has state for, newest checkpoint first."""
    seen = []
    for tup in checkpointer.list(None):
        session_id = tup.config["configurable"]["thread_id"]
        if session_id not in seen:
            seen.append(session_id)
    return seen


def session_exists(session_id: str) -> bool:
    config = {"configurable": {"thread_id": session_id}}
    return bool(app.get_state(config).values)


def initial_state(user_input: str) -> dict:
    """Fresh per-user-message counters; the accumulating lists are appended to."""
    return {
        "messages": [("user", user_input)],
        "generated_code": [],
        "execution_results": [],
        "execution_errors": [],
        "turn_count": 0,
        "retry_count": 0,
        "has_recent_error": False,
    }


def pending_interrupt(config: dict):
    """Return the interrupt blocking this thread, or None if it isn't paused."""
    snapshot = app.get_state(config)
    for interrupt in getattr(snapshot, "interrupts", ()) or ():
        return interrupt
    for task in getattr(snapshot, "tasks", ()) or ():
        for interrupt in getattr(task, "interrupts", ()) or ():
            return interrupt
    return None


def _render(event):
    for node_name, state_update in event.items():
        if not isinstance(state_update, dict):
            continue
        logger.debug("Update from node: %s", node_name)
        for message in state_update.get("messages", []):
            message.pretty_print()
        if state_update.get("generated_code"):
            for entry in state_update["generated_code"]:
                saved = f" -> {entry['filename']}" if entry.get("filename") else ""
                ran = f" [executed: {entry['execution_id']}]" if entry.get("execution_id") else ""
                logger.info("Code tracked: %s%s%s", entry["language"], saved, ran)
        if state_update.get("execution_errors"):
            logger.warning("Execution errors recorded: %d", len(state_update["execution_errors"]))
        if state_update.get("execution_results"):
            logger.info("Execution results recorded: %d", len(state_update["execution_results"]))


def _prompt_for_approval(payload) -> bool:
    requests = payload.get("requests", []) if isinstance(payload, dict) else []
    print("\n--- Approval required ---")
    for request in requests:
        print(f"[{request['tool']}] {request['description']}")
    answer = input("Approve? (y/n): ").strip().lower()
    return answer in ("y", "yes")


def loop(user_input: str, session_id: str = None):
    session_id = session_id or new_session_id()
    config = {"configurable": {"thread_id": session_id}}

    logger.info("Session %s | User: %s", session_id, user_input[:80])

    stream_input = initial_state(user_input)

    while True:
        for event in app.stream(stream_input, config=config, stream_mode="updates"):
            _render(event)

        interrupt = pending_interrupt(config)
        if interrupt is None:
            return

        stream_input = Command(resume=_prompt_for_approval(interrupt.value))
