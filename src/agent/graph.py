import uuid
import sqlite3
from langgraph.graph import START, END, StateGraph
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.prebuilt import ToolNode

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
