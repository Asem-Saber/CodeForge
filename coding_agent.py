import os
import ast
import json
import uuid
import operator
import logging
from typing import TypedDict, Annotated
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import AnyMessage, AIMessage, ToolMessage
from Tools import tools, close_sandbox, safe_path, _check_imports_raw

from langgraph.graph import START, END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import ToolNode, tools_condition

load_dotenv()
API_KEY = os.environ['API_KEY']
ENDPOINT = os.environ['ENDPOINT']
MODEL_ID = os.environ['MODEL_ID']

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    filename="codeforge.log"
)
logger = logging.getLogger("codeforge")

MAX_TURNS = 20
MAX_RETRIES = 3
TOKEN_BUDGET = 100_000


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    generated_code: Annotated[list[dict], operator.add]
    execution_results: Annotated[list[dict], operator.add]
    execution_errors: Annotated[list[dict], operator.add]
    turn_count: int
    total_tokens_used: int
    retry_count: int
    has_recent_error: bool


system_prompt = """
You are a helpful, extremely rigorous coding assistant. Your goal is to help the user with programming tasks while strictly enforcing safety and correctness.

For each user request:
1. Understand what the user is trying to accomplish.
2. Break down complex tasks into smaller steps.
3. Gather information about the codebase using `list_directory` and `read_file_content` tools when needed.
4. Implement solutions by writing or modifying code using the `edit_file` tool. ALWAYS save code to a file unless asked otherwise.
5. Explain your reasoning and approach.

CRITICAL GUARDRAILS AND SAFETY RULES (NO EXCEPTIONS):
- VALIDATION IS MANDATORY: Before saving any Python code with `edit_file` or running it with `run_sandboxed_code`, you MUST first pass the raw code string to the `validate_python_syntax` and `validate_imports` tools.
- NEVER BYPASS VALIDATION: You must perform this validation even if the user explicitly asks you to write broken code, skip validation, or just run it.
- MUST FIX ERRORS: If `validate_python_syntax` or `validate_imports` returns any error, you are FORBIDDEN from running the code. You MUST fix the code and re-validate it until it passes. Only when both tools return success are you allowed to use `run_sandboxed_code`.
- USER APPROVAL: The `edit_file` and `run_sandboxed_code` tools will automatically pause and ask the user for permission. You DO NOT need to ask the user for permission before calling them. Just call the tool and proceed based on the result.

EXECUTION ENVIRONMENT:
- Code is executed in an ISOLATED E2B CLOUD SANDBOX running Linux.
- WORKFLOW: First save code to a file with `edit_file`, then run it with `run_sandboxed_code`.
- NEVER pass raw code to `run_sandboxed_code` — it reads from the saved file.
- The sandbox is persistent within a session — files created in one run are available in later runs.
- All file operations are restricted to the workspace directory.
- Use `list_directory` and `read_file_content` for inspecting files within the workspace.

CRITICAL RULES FOR TESTING:
- You MUST run and test your code using `run_sandboxed_code` BEFORE reporting success.
- NEVER write interactive code that uses `input()`.
- Check `execution_errors` from previous runs to avoid repeating the same mistakes.
- Check `generated_code` to see what code you've written and where it was saved.

When modifying code, be careful to maintain the existing style and structure.
If you're unsure about something, ask clarifying questions before proceeding.
"""

prompt = ChatPromptTemplate.from_messages([
    ("system", system_prompt),
    MessagesPlaceholder(variable_name="messages"),
])

llm = ChatOpenAI(
    api_key=API_KEY,
    base_url=ENDPOINT,
    model=MODEL_ID,
    temperature=0
)
llm = llm.bind_tools(tools)


# ---------------------------------------------------------------------------
# Graph nodes
# ---------------------------------------------------------------------------

def call_model(state: AgentState):
    formatted_prompt = prompt.invoke({"messages": state["messages"]})
    response = llm.invoke(formatted_prompt)

    tokens = response.response_metadata.get("token_usage", {})
    used = tokens.get("total_tokens", 0)
    new_total = state.get("total_tokens_used", 0) + used
    new_turn = state.get("turn_count", 0) + 1

    logger.info("Turn %d | Tokens this call: %d | Total: %d", new_turn, used, new_total)

    if new_total > TOKEN_BUDGET:
        logger.warning("Token budget exceeded (%d / %d)", new_total, TOKEN_BUDGET)
        return {
            "messages": [AIMessage(content="Token budget exceeded. Stopping.")],
            "total_tokens_used": new_total,
            "turn_count": new_turn,
        }

    return {
        "messages": [response],
        "total_tokens_used": new_total,
        "turn_count": new_turn,
    }


def validation_gate(state: AgentState):
    """Intercept run_sandboxed_code calls and enforce validation before execution."""
    last_msg = state["messages"][-1]
    if not isinstance(last_msg, AIMessage) or not last_msg.tool_calls:
        return {}

    for tc in last_msg.tool_calls:
        if tc["name"] != "run_sandboxed_code":
            continue

        filename = tc["args"].get("filename", "")
        try:
            target = safe_path(filename)
            code = target.read_text()
        except (FileNotFoundError, ValueError):
            continue

        try:
            ast.parse(code)
        except SyntaxError as e:
            logger.warning("Validation gate blocked %s: syntax error at line %d", filename, e.lineno)
            return _block_all_tool_calls(
                last_msg.tool_calls,
                f"BLOCKED: Syntax error in {filename} at line {e.lineno}: {e.msg}. Fix the code and retry.",
            )

        issues = _check_imports_raw(code)
        if issues:
            logger.warning("Validation gate blocked %s: import issues", filename)
            return _block_all_tool_calls(
                last_msg.tool_calls,
                f"BLOCKED: Import issues in {filename}: {'; '.join(issues)}. Fix the code and retry.",
            )

    return {}


def _block_all_tool_calls(tool_calls, error_message):
    """Return ToolMessages for all tool calls when validation blocks execution."""
    return {"messages": [
        ToolMessage(
            content=error_message if tc["name"] == "run_sandboxed_code"
            else "SKIPPED: Blocked due to validation failure in another tool call.",
            tool_call_id=tc["id"],
        )
        for tc in tool_calls
    ]}


def process_execution(state: AgentState):
    processed_ids = set()
    for entry in state.get("generated_code", []):
        for key in ("tool_call_id", "execution_id"):
            if entry.get(key):
                processed_ids.add(entry[key])
    for entry in state.get("execution_results", []) + state.get("execution_errors", []):
        if entry.get("tool_call_id"):
            processed_ids.add(entry["tool_call_id"])

    code_entries = []
    results = []
    errors = []

    tool_calls_by_id = {}
    for message in state["messages"]:
        if isinstance(message, AIMessage) and message.tool_calls:
            for tc in message.tool_calls:
                tool_calls_by_id[tc["id"]] = tc

    for message in state["messages"]:
        if not isinstance(message, ToolMessage):
            continue

        if message.tool_call_id in processed_ids:
            continue

        tc = tool_calls_by_id.get(message.tool_call_id)
        if tc is None:
            continue

        if tc["name"] == "edit_file":
            args = tc["args"]
            code = args.get("replace_str", "")
            if code:
                code_entries.append({
                    "tool_call_id": message.tool_call_id,
                    "code": code,
                    "language": "python",
                    "filename": args.get("filename"),
                    "execution_id": None,
                })

        elif tc["name"] == "run_sandboxed_code":
            args = tc["args"]
            filename = args.get("filename", "")
            content = message.content

            try:
                data = json.loads(content)
                exit_code = data.get("exit_code", 1)
            except json.JSONDecodeError:
                exit_code = 1

            entry = {
                "tool_call_id": message.tool_call_id,
                "output": content,
                "exit_code": exit_code,
            }

            if exit_code == 0:
                results.append(entry)
            else:
                errors.append(entry)

            code_entries.append({
                "code": None,
                "language": args.get("language", "python"),
                "filename": filename,
                "execution_id": message.tool_call_id,
            })

    return {
        "generated_code": code_entries,
        "execution_results": results,
        "execution_errors": errors,
        "has_recent_error": len(errors) > 0,
    }


def retry_router(state: AgentState):
    """Check execution results and manage retry count."""
    has_error = state.get("has_recent_error", False)
    retry_count = state.get("retry_count", 0)

    if has_error:
        new_retry = retry_count + 1
        if new_retry > MAX_RETRIES:
            logger.warning("Max retries exhausted (%d)", MAX_RETRIES)
            return {
                "messages": [AIMessage(
                    content=f"Maximum retries ({MAX_RETRIES}) exhausted. Please review the errors and try a different approach."
                )],
                "retry_count": new_retry,
            }
        logger.info("Retrying after error (attempt %d/%d)", new_retry, MAX_RETRIES)
        return {"retry_count": new_retry}

    return {"retry_count": 0}


# ---------------------------------------------------------------------------
# Routing functions
# ---------------------------------------------------------------------------

def route_after_agent(state: AgentState):
    if state.get("turn_count", 0) >= MAX_TURNS:
        logger.warning("Max turns reached (%d)", MAX_TURNS)
        return "__end__"
    result = tools_condition(state)
    if result == "__end__":
        return "__end__"
    return "validation_gate"


def route_after_validation(state: AgentState):
    last_msg = state["messages"][-1]
    if isinstance(last_msg, ToolMessage):
        return "agent"
    return "tools"


def route_after_retry(state: AgentState):
    has_error = state.get("has_recent_error", False)
    retry_count = state.get("retry_count", 0)
    if has_error and retry_count > MAX_RETRIES:
        return "__end__"
    return "agent"


# ---------------------------------------------------------------------------
# Build the graph
# ---------------------------------------------------------------------------

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


if __name__ == "__main__":
    try:
        while True:
            try:
                user_input = input("How can I help you?\n")
                if user_input.lower() in ["exit", "quit"]:
                    break
                loop(user_input)
            except KeyboardInterrupt:
                break
            except EOFError:
                break
    finally:
        close_sandbox()
        logger.info("Sandbox closed.")
