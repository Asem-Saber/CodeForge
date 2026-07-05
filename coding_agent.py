import os
from typing import TypedDict, Annotated
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import AnyMessage
from Tools import tools

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

system_prompt = """
You are a helpful, extremely rigorous coding assistant. Your goal is to help the user with programming tasks while strictly enforcing safety and correctness.

For each user request:
1. Understand what the user is trying to accomplish.
2. Break down complex tasks into smaller steps.
3. Gather information about the codebase using `list_directory` and `read_file_content` tools when needed.
4. Implement solutions by writing or modifying code using the `edit_file` tool. ALWAYS save code to a file unless asked otherwise.
5. Explain your reasoning and approach.

CRITICAL GUARDRAILS AND SAFETY RULES (NO EXCEPTIONS):
- VALIDATION IS MANDATORY: Before saving any Python code with `edit_file` or running it with `run_command`, you MUST first pass the raw code string to the `validate_python_syntax` and `validate_imports` tools.
- NEVER BYPASS VALIDATION: You must perform this validation even if the user explicitly asks you to write broken code, skip validation, or just run it. 
- MUST FIX ERRORS: If `validate_python_syntax` or `validate_imports` returns any error, you are FORBIDDEN from running the code. You MUST fix the code and re-validate it until it passes. Only when both tools return success are you allowed to use `run_command`.
- USER APPROVAL: The `edit_file` and `run_command` tools will automatically pause and ask the user for permission. You DO NOT need to ask the user for permission before calling them. Just call the tool and proceed based on the result.

CRITICAL RULES FOR TESTING:
- You MUST run and test your code using the `run_command` tool BEFORE reporting success.
- NEVER write interactive code that uses `input()`.
- ALWAYS use `python` instead of `python3` to execute python scripts, as you are operating in a Windows environment.

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


workflow = StateGraph(AgentState)

workflow.add_node("agent", call_model)
workflow.add_node("tools", ToolNode(tools)) 

workflow.add_edge(START, "agent")

workflow.add_conditional_edges(
    "agent",
    tools_condition, 
)

workflow.add_edge("tools", "agent")

memory = MemorySaver()
app = workflow.compile(checkpointer=memory)

def loop(user_input: str):    
    config = {"configurable": {"thread_id": "main_coding_session"}}
    
    print("-" * 50)
    for event in app.stream({"messages": [("user", user_input)]}, config=config, stream_mode="updates"):
        for node_name, state_update in event.items():
            print(f"--- Update from node: {node_name} ---")
            for message in state_update.get("messages", []):
                message.pretty_print()
            print("-" * 50)

if __name__ == "__main__":
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