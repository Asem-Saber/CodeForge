import ast
import json
import logging
from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import tools_condition
from langgraph.types import interrupt

from src.config import API_KEY, ENDPOINT, MODEL_ID, MAX_TURNS, MAX_RETRIES, TOKEN_BUDGET
from src.prompts import prompt
from src.tools import tools
from src.tools.validation import _check_imports_raw
from src.sandbox.paths import safe_path, session_id_from_config
from src.agent.state import AgentState

logger = logging.getLogger("codeforge")

# Tools whose side effects leave the agent's sandbox reasoning and touch the
# user's workspace or execute code — these need explicit human sign-off.
APPROVAL_REQUIRED = {"edit_file", "run_sandboxed_code"}

llm = ChatOpenAI(
    api_key=API_KEY,
    base_url=ENDPOINT,
    model=MODEL_ID,
    temperature=0,
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


def validation_gate(state: AgentState, config: RunnableConfig = None):
    """Intercept run_sandboxed_code calls and enforce validation before execution."""
    last_msg = state["messages"][-1]
    if not isinstance(last_msg, AIMessage) or not last_msg.tool_calls:
        return {}

    session_id = session_id_from_config(config)

    for tc in last_msg.tool_calls:
        if tc["name"] != "run_sandboxed_code":
            continue

        filename = tc["args"].get("filename", "")
        try:
            target = safe_path(filename, session_id)
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

        issues = _check_imports_raw(code, session_id)
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


def _describe_tool_call(tc) -> str:
    """One-line, human-readable summary of a pending side-effecting tool call."""
    args = tc.get("args", {})
    name = tc["name"]
    if name == "edit_file":
        filename = args.get("filename", "?")
        verb = "create" if args.get("find_str", "") == "" else "modify"
        body = args.get("replace_str", "") or ""
        preview = body if len(body) <= 400 else body[:400] + "\n[...truncated...]"
        return f"{verb} {filename}\n{preview}"
    if name == "run_sandboxed_code":
        return f"run {args.get('filename', '?')} ({args.get('language', 'python')}) in the sandbox"
    return f"{name}({args})"


def _resolve_approval(decision, pending) -> dict:
    """Normalize a resume value into {tool_call_id: approved}.

    Accepts a bool, a yes/no string, or a per-call {tool_call_id: bool} mapping.
    Anything unrecognized is treated as a denial — failing closed is the only
    safe default for a gate that guards code execution.
    """
    if isinstance(decision, dict):
        return {tc["id"]: bool(decision.get(tc["id"], False)) for tc in pending}
    if isinstance(decision, str):
        approved = decision.strip().lower() in ("y", "yes", "approve", "approved", "true")
    else:
        approved = decision is True
    return {tc["id"]: approved for tc in pending}


def approval_gate(state: AgentState):
    """Pause the graph for human sign-off before any side-effecting tool runs.

    Resuming with Command(resume=...) re-executes this node from the top, so it
    must stay free of side effects — it only inspects state and emits messages.
    """
    last_msg = state["messages"][-1]
    if not isinstance(last_msg, AIMessage) or not last_msg.tool_calls:
        return {}

    pending = [tc for tc in last_msg.tool_calls if tc["name"] in APPROVAL_REQUIRED]
    if not pending:
        return {}

    decision = interrupt({
        "type": "approval_request",
        "requests": [
            {
                "tool_call_id": tc["id"],
                "tool": tc["name"],
                "args": tc.get("args", {}),
                "description": _describe_tool_call(tc),
            }
            for tc in pending
        ],
    })

    approvals = _resolve_approval(decision, pending)
    denied = [tc for tc in pending if not approvals.get(tc["id"])]
    if not denied:
        return {}

    logger.info("User denied %d of %d pending tool call(s)", len(denied), len(pending))
    denied_ids = {tc["id"] for tc in denied}
    return {"messages": [
        ToolMessage(
            content="CANCELLED: The user declined this action."
            if tc["id"] in denied_ids
            else "SKIPPED: Not run because the user declined another action in this batch.",
            tool_call_id=tc["id"],
        )
        for tc in last_msg.tool_calls
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
    """Blocked calls leave a ToolMessage behind; otherwise go ask for approval."""
    last_msg = state["messages"][-1]
    if isinstance(last_msg, ToolMessage):
        return "agent"
    return "approval_gate"


def route_after_approval(state: AgentState):
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
