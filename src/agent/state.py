import operator
from typing import TypedDict, Annotated
from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    generated_code: Annotated[list[dict], operator.add]
    execution_results: Annotated[list[dict], operator.add]
    execution_errors: Annotated[list[dict], operator.add]
    turn_count: int
    total_tokens_used: int
    retry_count: int
    has_recent_error: bool
