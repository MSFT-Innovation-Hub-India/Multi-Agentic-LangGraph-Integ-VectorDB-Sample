from dotenv import load_dotenv
import os
from langchain_openai import AzureChatOpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

# import service_requests.db_tools as db_tools
from service_requests.db_tools import (
    fetch_customer_information,
    get_available_service_slots,
    create_service_appointment_slot,
    store_service_feedback
)

# import service_requests.search_tools as search_tools
from service_requests.search_tools import perform_search_based_qna

from langchain_core.tools import tool
from langgraph.prebuilt import tools_condition
from typing_extensions import TypedDict
from langgraph.graph import StateGraph, START, END
from typing import Callable

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableLambda

from langgraph.prebuilt import ToolNode
from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver

from typing_extensions import TypedDict
from langgraph.graph import MessagesState
from langgraph.types import Command

import traceback
import uuid
import datetime

from IPython.display import display, Image


from typing import Annotated, Literal, Optional
from typing_extensions import TypedDict
from langgraph.graph.message import AnyMessage, add_messages

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable, RunnableConfig
from langgraph.graph import StateGraph
from pydantic import BaseModel, Field

### $env:TAVILY_API_KEY = "tvly-<yourkey>"

load_dotenv()
az_openai_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
az_openai_deployment_name = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
az_openai_embedding_deployment_name = os.getenv(
    "AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT_NAME"
)
az_api_type = os.getenv("API_TYPE")
az_openai_version = os.getenv("API_VERSION")

credential = DefaultAzureCredential()
token_provider = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")

llm = AzureChatOpenAI(
    azure_endpoint=az_openai_endpoint,
    azure_deployment=az_openai_deployment_name,
    azure_ad_token_provider=token_provider,
    openai_api_type=az_api_type,
    api_version=az_openai_version,
)

thread_id = str(uuid.uuid4())
config = {
    "configurable": {
        # The customer name is used in to
        # fetch the customer's service appointment history information
        "customer_name": "Ravi Kumar",
        "thread_id": thread_id,
    }
}


def update_dialog_stack(left: list[str], right: Optional[str]) -> list[str]:
    """Push or pop the state."""
    if right is None:
        return left
    if right == "pop":
        return left[:-1]
    return left + [right]


class State(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    customer_info: str
    dialog_state: Annotated[
        list[Literal["assistant", "service_scheduling", "search_qna", "service_feedback"]],
        update_dialog_stack,
    ]


class Assistant:
    def __init__(self, runnable: Runnable):
        self.runnable = runnable

    def __call__(self, state: State, config: RunnableConfig):
        while True:
            result = self.runnable.invoke(state)

            if not result.tool_calls and (
                not result.content
                or isinstance(result.content, list)
                and not result.content[0].get("text")
            ):
                messages = state["messages"] + [("user", "Respond with a real output.")]
                state = {**state, "messages": messages}
            else:
                break
        return {"messages": result}


class CompleteOrEscalate(BaseModel):
    """A tool to mark the current task as completed and/or to escalate control of the dialog to the main assistant,
    who can re-route the dialog based on the user's needs."""

    cancel: bool = True
    reason: str

    class Config:
        json_schema_extra = {
            "example": {
                "cancel": True,
                "reason": "User changed their mind about the current task.",
            },
            "example 2": {
                "cancel": True,
                "reason": "I have fully completed the task.",
            },
            "example 3": {
                "cancel": False,
                "reason": "I need to search the user's emails or calendar for more information.",
            },
        }


# Service Scheduling Assistant
service_scheduling_prompt = ChatPromptTemplate(
    [
        (
            "system",
            "You are a specialized assistant for handling service scheduling for customers. "
            " The primary assistant delegates work to you whenever the user needs help scheduling appointments or querying existing ones. "
            "Confirm the appointment with the customer and inform them of any additional fees. "
            " When searching, be persistent. Expand your query bounds if the first search returns no results. "
            "If you need more information or the customer changes their mind, escalate the task back to the main assistant."
            " Remember that a service schedule booking isn't completed until after the relevant tool has successfully been used."
            "\n\nCurrent customer information:\n<Customer_service_records>\n{customer_info}\n</Customer_service_records>"
            "\nCurrent time: {time}."
            "\n\nIf the user needs help, and none of your tools are appropriate for it, then"
            ' "CompleteOrEscalate" the dialog to the host assistant. Do not waste the user\'s time. Do not make up invalid tools or functions.',
        ),
        ("placeholder", "{messages}"),
    ]
).partial(time=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

service_scheduling_tools = [
    get_available_service_slots,
    create_service_appointment_slot,
]
service_scheduling_runnable = service_scheduling_prompt | llm.bind_tools(
    service_scheduling_tools + [CompleteOrEscalate]
)

# Service feedback Assistant
service_feedback_prompt = ChatPromptTemplate(
    [
        (
            "system",
            " You are a specialized assistant for handling service feedback capture from customers. "
            " The primary assistant delegates work to you whenever the user needs to provide feedback on the service provided. "
            " Ensure that the appointment status corresponding to the service id is complete before you let the user provide feedback. "
            " Do not ask the user for feedback on all the aspects of the service. Ask the overall rating and feedback first. "
            " Next ask them, if they would like to provide feedback on any of quality of work done during the servicing, the cleanliness of the vehicle, timeliness of the service, or courteousness of the staff. "
            " Based on the response, prompt the customer to provide a rating and comments for those specifi aspects alone. "
            " Once the customer provides their feedback, display the feedback provided and ask for confirmation. They could choose to change any specific aspect of the feedback provided. "
            " Once confirmation is received, thank them for their time and inform them that the feedback has been captured. "
            " Remember that a service schedule booking isn't completed until after the relevant tool has successfully been used. "
            "\n\nCurrent customer information:\n<Customer_service_records>\n{customer_info}\n</Customer_service_records> "
            "\nCurrent time: {time}."
            "\n\nIf the user needs help, and none of your tools are appropriate for it, then"
            ' "CompleteOrEscalate" the dialog to the host assistant. Do not waste the user\'s time. Do not make up invalid tools or functions.',
        ),
        ("placeholder", "{messages}"),
    ]
).partial(time=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

service_feedback_tools = [
    store_service_feedback,
]
service_feedback_runnable = service_feedback_prompt | llm.bind_tools(
    service_feedback_tools + [CompleteOrEscalate]
)


search_qna_prompt = ChatPromptTemplate(
    [
        (
            "system",
            "You are a specialized assistant for handling search queries. Refer to the context provided to you to answer the user's questions. "
            " The primary assistant delegates work to you whenever the user needs help searching for information. "
            " When searching, be persistent. Expand your query bounds if the first search returns no results. "
            "If you need more information or the customer changes their mind, escalate the task back to the main assistant."
            " Remember that a search query isn't completed until after the relevant tool has successfully been used."
            "\n\nCurrent customer information:\n<Customer_service_records>\n{customer_info}\n</Customer_service_records>"
            "\nCurrent time: {time}."
            "\n\nIf the user needs help, and none of your tools are appropriate for it, then"
            ' "CompleteOrEscalate" the dialog to the host assistant. Do not waste the user\'s time. Do not make up invalid tools or functions.',
        ),
        ("placeholder", "{messages}"),
    ]
).partial(time=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

search_qna_tools = [perform_search_based_qna]
search_qna_runnable = search_qna_prompt | llm.bind_tools(
    search_qna_tools + [CompleteOrEscalate]
)


class ToServiceScheduler(BaseModel):
    """Transfers work to a specialized assistant to handle vehicle service scheduling."""

    request: str = Field(
        description="Any additional information or requests from the user regarding the service scheduling."
    )
    start_date: str = Field(
        description="The date on which the service appointments are sought."
    )
    customer_name: str = Field(
        description="The name of the Customer on which the service appointment is to be scheduled."
    )

    class Config:
        json_schema_extra = {
            "example": {
                "request": "I am looking for service appointments for my vehicle.",
                "start_date": "2023-07-01",
                "customer_name": "some user name",
            }
        }

class ToServiceFeedback(BaseModel):
    """Transfers work to a specialized assistant to handle vehicle service feedback capture."""

    request: str = Field(
        description="Any additional information or requests from the user regarding the service feedback."
    )
    schedule_id: int = Field(
        description="The schedule id in the system for the vehicle servicing."
    )
    customer_id: int = Field(
        description="The id of the Customer against which the service appointment was scheduled and completed."
    )
    
    overall_rating: int = Field(
        description="The overall rating provided by the customer for the service provided."
    )
    overall_comments: str = Field(
        description="The overall comments provided by the customer for the service provided."
    )

    class Config:
        json_schema_extra = {
            "example": {
                "request": "Here is my feedback on the servicing of my vehicle.",
                "schedule_id": 1,
                "customer_id": 1,
                "overall_rating": 5,
                "overall_comments": "The service was excellent. I am very satisfied with the service provided.",
            }
        }

class ToSearchQnA(BaseModel):
    """Transfers work to a specialized assistant to handle vehicle service scheduling."""

    query: str = Field(description="the search query to be performed.")

    class Config:
        json_schema_extra = {
            "example": {
                "query": "What are the safety features in my bike?",
            }
        }


# Primary Assistant
primary_assistant_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are a helpful customer support assistant for Contoso motocorp. "
            "Your primary role is to help customers book service appointments for their vehicles and to answer customer queries. "
            "If a customer requests to update or cancel a flight, book a car rental, book a hotel, or get trip recommendations, "
            "delegate the task to the appropriate specialized assistant by invoking the corresponding tool. You are not able to make these types of changes yourself."
            " Only the specialized assistants are given permission to do this for the user."
            "The user is not aware of the different specialized assistants, so do not mention them; just quietly delegate through function calls. "
            "Provide detailed information to the customer, and always double-check the database before concluding that information is unavailable. "
            " When searching, be persistent. Expand your query bounds if the first search returns no results. "
            " If a search comes up empty, expand your search before giving up."
            "\n\nCurrent customer information:\n<Customer_service_records>\n{customer_info}\n</Customer_service_records>"
            "\nCurrent time: {time}.",
        ),
        ("placeholder", "{messages}"),
    ]
).partial(time=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

assistant_runnable = primary_assistant_prompt | llm.bind_tools(
    [ToServiceScheduler, ToSearchQnA, ToServiceFeedback]
)


def create_entry_node(assistant_name: str, new_dialog_state: str) -> Callable:
    def entry_node(state: State) -> dict:
        tool_call_id = state["messages"][-1].tool_calls[0]["id"]
        return {
            "messages": [
                ToolMessage(
                    content=f"The assistant is now the {assistant_name}. Reflect on the above conversation between the host assistant and the user."
                    f" The user's intent is unsatisfied. Use the provided tools to assist the user. Remember, you are {assistant_name},"
                    " and the booking, update, other other action is not complete until after you have successfully invoked the appropriate tool."
                    " If the user changes their mind or needs help for other tasks, call the CompleteOrEscalate function to let the primary host assistant take control."
                    " Do not mention who you are - just act as the proxy for the assistant.",
                    tool_call_id=tool_call_id,
                )
            ],
            "dialog_state": new_dialog_state,
        }

    return entry_node


def handle_tool_error(state) -> dict:
    error = state.get("error")
    tool_calls = state["messages"][-1].tool_calls
    return {
        "messages": [
            ToolMessage(
                content=f"Error: {repr(error)}\n please fix your mistakes.",
                tool_call_id=tc["id"],
            )
            for tc in tool_calls
        ]
    }


def create_tool_node_with_fallback(tools: list) -> dict:
    return ToolNode(tools).with_fallbacks(
        [RunnableLambda(handle_tool_error)], exception_key="error"
    )


def _print_event(event: dict, _printed: set, max_length=1500):
    current_state = event.get("dialog_state")
    if current_state:
        print("Currently in: ", current_state[-1])
    message = event.get("messages")
    if message:
        if isinstance(message, list):
            message = message[-1]
        if message.id not in _printed:
            msg_repr = message.pretty_repr(html=True)
            if len(msg_repr) > max_length:
                msg_repr = msg_repr[:max_length] + " ... (truncated)"
            print(msg_repr)
            _printed.add(message.id)


builder = StateGraph(State)


def customer_info(state: State):
    if state.get("customer_info"):
        return {"customer_info": state.get("customer_info")}
    else:
        return {"customer_info": fetch_customer_information.invoke({})}


builder.add_node("fetch_customer_info", customer_info)
builder.add_edge(START, "fetch_customer_info")


# service schedling assistant
builder.add_node(
    "enter_service_scheduling",
    create_entry_node("Service Scheduling Assistant", "service_scheduling"),
)
builder.add_node("service_scheduling", Assistant(service_scheduling_runnable))
builder.add_edge("enter_service_scheduling", "service_scheduling")
builder.add_node(
    "service_scheduling_tools", create_tool_node_with_fallback(service_scheduling_tools)
)


def route_service_scheduling(state: State):
    route = tools_condition(state)
    if route == END:
        return END
    tool_calls = state["messages"][-1].tool_calls
    did_cancel = any(tc["name"] == CompleteOrEscalate.__name__ for tc in tool_calls)
    if did_cancel:
        return "leave_skill"

    # for t in service_scheduling_tools:
    #     print(t)
    # safe_toolnames = [t.name for t in service_scheduling_tools]
    safe_toolnames = [
        t.name if hasattr(t, "name") else t.__name__ for t in service_scheduling_tools
    ]
    if all(tc["name"] in safe_toolnames for tc in tool_calls):
        return "service_scheduling_tools"
    return None


builder.add_edge("service_scheduling_tools", "service_scheduling")
builder.add_conditional_edges(
    "service_scheduling",
    route_service_scheduling,
    ["service_scheduling_tools", "leave_skill", END],
)

# service feedback assistant
builder.add_node(
    "enter_service_feedback",
    create_entry_node("Service feedback Assistant", "service_feedback"),
)
builder.add_node("service_feedback", Assistant(service_feedback_runnable))
builder.add_edge("enter_service_feedback", "service_feedback")
builder.add_node(
    "service_feedback_tools", create_tool_node_with_fallback(service_feedback_tools)
)

def route_service_feedback(state: State):
    route = tools_condition(state)
    if route == END:
        return END
    tool_calls = state["messages"][-1].tool_calls
    did_cancel = any(tc["name"] == CompleteOrEscalate.__name__ for tc in tool_calls)
    if did_cancel:
        return "leave_skill"
    safe_toolnames = [
        t.name if hasattr(t, "name") else t.__name__ for t in service_feedback_tools
    ]
    if all(tc["name"] in safe_toolnames for tc in tool_calls):
        return "service_feedback_tools"
    return None

builder.add_edge("service_feedback_tools", "service_feedback")
builder.add_conditional_edges(
    "service_feedback",
    route_service_feedback,
    ["service_feedback_tools", "leave_skill", END],
)

# This node will be shared for exiting all specialized assistants
def pop_dialog_state(state: State) -> dict:
    """Pop the dialog stack and return to the main assistant.

    This lets the full graph explicitly track the dialog flow and delegate control
    to specific sub-graphs.
    """
    messages = []
    if state["messages"][-1].tool_calls:
        # Note: Doesn't currently handle the edge case where the llm performs parallel tool calls
        messages.append(
            ToolMessage(
                content="Resuming dialog with the host assistant. Please reflect on the past conversation and assist the user as needed.",
                tool_call_id=state["messages"][-1].tool_calls[0]["id"],
            )
        )
    return {"dialog_state": "pop", "messages": messages}


builder.add_node("leave_skill", pop_dialog_state)
builder.add_edge("leave_skill", "primary_assistant")

# QnA assistant

builder.add_node(
    "enter_search_qna",
    create_entry_node("Search Q&A Assistant", "search_qna"),
)
builder.add_node("search_qna", Assistant(search_qna_runnable))
builder.add_edge("enter_search_qna", "search_qna")
builder.add_node("search_qna_tools", create_tool_node_with_fallback(search_qna_tools))


def route_search_qna(state: State):
    route = tools_condition(state)
    if route == END:
        return END
    tool_calls = state["messages"][-1].tool_calls
    did_cancel = any(tc["name"] == CompleteOrEscalate.__name__ for tc in tool_calls)
    if did_cancel:
        return "leave_skill"
    safe_toolnames = [t.name for t in search_qna_tools]
    if all(tc["name"] in safe_toolnames for tc in tool_calls):
        return "search_qna_tools"
    return "None"


builder.add_edge("search_qna_tools", "search_qna")
builder.add_conditional_edges(
    "search_qna",
    route_search_qna,
    [
        "search_qna_tools",
        "leave_skill",
        END,
    ],
)

# Primary assistant
builder.add_node("primary_assistant", Assistant(assistant_runnable))


def route_primary_assistant(
    state: State,
):
    route = tools_condition(state)
    if route == END:
        return END
    tool_calls = state["messages"][-1].tool_calls
    if tool_calls:
        if tool_calls[0]["name"] == ToServiceScheduler.__name__:
            return "enter_service_scheduling"
        elif tool_calls[0]["name"] == ToSearchQnA.__name__:
            return "enter_search_qna"
        elif tool_calls[0]["name"] == ToServiceFeedback.__name__:
            return "enter_service_feedback"
        return None
    raise ValueError("Invalid route")


# The assistant can route to one of the delegated assistants,
# directly use a tool, or directly respond to the user
builder.add_conditional_edges(
    "primary_assistant",
    route_primary_assistant,
    ["enter_service_scheduling", "enter_search_qna","enter_service_feedback", END],
)
# builder.add_edge("primary_assistant_tools", "primary_assistant")


# Each delegated workflow can directly respond to the user
# When the user responds, we want to return to the currently active workflow
def route_to_workflow(
    state: State,
) -> Literal["primary_assistant", "service_scheduling", "search_qna","service_feedback"]:
    """If we are in a delegated state, route directly to the appropriate assistant."""
    dialog_state = state.get("dialog_state")
    if not dialog_state:
        return "primary_assistant"
    return dialog_state[-1]


builder.add_conditional_edges("fetch_customer_info", route_to_workflow)

# Compile graph
memory = MemorySaver()
graph = builder.compile(checkpointer=memory)

# graph_image = graph.get_graph().draw_mermaid_png()
# with open("graph_bot_app_v2.png", "wb") as f:
#     f.write(graph_image)
# display(Image("graph_bot_app_v2.png"))


def stream_graph_updates(user_input: str):
    events = graph.stream(
        {"messages": [("user", user_input)]},
        config,
        subgraphs=True,
        stream_mode="values",
    )
    l_events = list(events)
    msg = list(l_events[-1])
    r1 = msg[-1]["messages"]
    # response_to_user = msg[-1].messages[-1].content

    print(r1[-1].content)


while True:
    try:
        user_input = input("User: ")
        if user_input.lower() in ["quit", "exit", "q"]:
            print("Goodbye!")
            break

        stream_graph_updates(user_input)
    except Exception as e:
        print("An error occurred:", e)
        traceback.print_exc()
        # stream_graph_updates(user_input)
        break
