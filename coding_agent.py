import os
import operator
from typing import TypedDict, Annotated
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import AnyMessage, AIMessage, ToolMessage
from Tools import tools, close_sandbox

from langgraph.graph import START, END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import ToolNode, tools_condition

load_dotenv() 
API_KEY = os.environ['API_KEY']
ENDPOINT = os.environ['ENDPOINT']
MODEL_ID = os.environ['MODEL_ID'] 

class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    generated_code: Annotated[list[dict], operator.add]
    execution_results: Annotated[list[dict], operator.add]
    execution_errors: Annotated[list[dict], operator.add]

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
- Use `list_directory` and `read_file_content` for inspecting the LOCAL filesystem only.

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



def call_model(state: AgentState):
    formatted_prompt = prompt.invoke({"messages": state["messages"]})
    response = llm.invoke(formatted_prompt)
    return {"messages": [response]}


def process_execution(state: AgentState):
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

        tc = tool_calls_by_id.get(message.tool_call_id)
        if tc is None:
            continue

        if tc["name"] == "edit_file":
            args = tc["args"]
            code = args.get("replace_str", "")
            if code:
                code_entries.append({
                    "code": code,
                    "language": "python",
                    "filename": args.get("filename"),
                    "execution_id": None,
                })

        elif tc["name"] == "run_sandboxed_code":
            args = tc["args"]
            filename = args.get("filename", "")
            content = message.content
            exit_code = None
            for line in content.split("\n"):
                if line.startswith("EXIT_CODE:"):
                    try:
                        exit_code = int(line.split(":")[1].strip())
                    except ValueError:
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
    }


workflow = StateGraph(AgentState)

workflow.add_node("agent", call_model)
workflow.add_node("tools", ToolNode(tools))
workflow.add_node("process_execution", process_execution)

workflow.add_edge(START, "agent")

workflow.add_conditional_edges(
    "agent",
    tools_condition,
)

workflow.add_edge("tools", "process_execution")
workflow.add_edge("process_execution", "agent")

memory = MemorySaver()
app = workflow.compile(checkpointer=memory)

def loop(user_input: str):
    config = {"configurable": {"thread_id": "main_coding_session"}}

    print("-" * 50)
    for event in app.stream(
        {"messages": [("user", user_input)], "generated_code": [], "execution_results": [], "execution_errors": []},
        config=config,
        stream_mode="updates",
    ):
        for node_name, state_update in event.items():
            print(f"--- Update from node: {node_name} ---")
            for message in state_update.get("messages", []):
                message.pretty_print()
            if state_update.get("generated_code"):
                for entry in state_update["generated_code"]:
                    saved = f" → {entry['filename']}" if entry.get("filename") else ""
                    ran = f" [executed: {entry['execution_id']}]" if entry.get("execution_id") else ""
                    print(f"[Code tracked: {entry['language']}{saved}{ran}]")
            if state_update.get("execution_errors"):
                print(f"[Execution errors recorded: {len(state_update['execution_errors'])}]")
            if state_update.get("execution_results"):
                print(f"[Execution results recorded: {len(state_update['execution_results'])}]")
            print("-" * 50)

if __name__ == "__main__":
    try:
        while True:
            try:
                user_input = input("How can I help you?\n")
                if user_input.lower() in ['exit', 'quit']:
                    break
                loop(user_input)
            except KeyboardInterrupt:
                break
            except EOFError:
                break
    finally:
        close_sandbox()
        print("Sandbox closed.")