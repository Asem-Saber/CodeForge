import uuid
import logging
from langgraph.graph import START, END, StateGraph
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import ToolNode

from src.tools import tools
from src.agent.state import AgentState
from src.agent.nodes import (
    call_model,
    validation_gate,
    process_execution,
    retry_router,
    route_after_agent,
    route_after_validation,
    route_after_retry,
)

logger = logging.getLogger("codeforge")

workflow = StateGraph(AgentState)

workflow.add_node("agent", call_model)
workflow.add_node("validation_gate", validation_gate)
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
    "tools": "tools",
})

workflow.add_edge("tools", "process_execution")
workflow.add_edge("process_execution", "retry_router")

workflow.add_conditional_edges("retry_router", route_after_retry, {
    "__end__": END,
    "agent": "agent",
})

memory = MemorySaver()
app = workflow.compile(checkpointer=memory)


def loop(user_input: str, session_id: str = None):
    session_id = session_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": session_id}}

    logger.info("Session %s | User: %s", session_id, user_input[:80])

    for event in app.stream(
        {
            "messages": [("user", user_input)],
            "generated_code": [],
            "execution_results": [],
            "execution_errors": [],
            "turn_count": 0,
            "retry_count": 0,
            "has_recent_error": False,
        },
        config=config,
        stream_mode="updates",
    ):
        for node_name, state_update in event.items():
            if state_update is None:
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
