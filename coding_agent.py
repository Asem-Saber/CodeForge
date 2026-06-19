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
API_KEY = os.environ['GITHUB_API_KEY']
ENDPOINT = "https://models.github.ai/inference"
MODEL_ID = "gpt-4o" 

class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]

system_prompt = """
You are a helpful coding assistant. Your goal is to help the user with programming tasks.

For each user request:
1. Understand what the user is trying to accomplish
2. Break down complex tasks into smaller steps
3. Use your tools to gather information about the codebase when needed
4. Implement solutions by writing or modifying code using the edit_file tool. ALWAYS save code to a file unless asked otherwise.
5. Explain your reasoning and approach

CRITICAL RULES FOR TESTING:
- You MUST run and test your code using the `run_command` tool BEFORE reporting success.
- DO NOT ask for permission to test the code. Just run it.
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
    
    for event in app.stream({"messages": [("user", user_input)]}, config=config, stream_mode="values"):
        pass
    
    final_message = event["messages"][-1]
    print(final_message.content)



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