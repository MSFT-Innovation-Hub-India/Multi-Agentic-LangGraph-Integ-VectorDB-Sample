from dotenv import load_dotenv
import os
from langchain_openai import AzureChatOpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
import service_requests.db_tools as db_tools
import service_requests.search_tools as search_tools

from langchain_core.tools import tool
from typing import Annotated
from typing import Literal
from typing_extensions import TypedDict
from langgraph.graph import StateGraph, START, END


from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import create_react_agent

from typing import Literal
from typing_extensions import TypedDict
from langgraph.graph import MessagesState
from langgraph.types import Command

import traceback
import uuid

from IPython.display import display, Image

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

# config = {
#     "configurable": {
#         # The passenger_id is used in our flight tools to
#         # fetch the user's flight information
#         "customer_name": "Ravi Kumar",
#         "vehicle_id":1,
#         "service_type_id":1,
#         # Checkpoints are accessed by thread_id
#         "thread_id": thread_id,
#     }
# }



members = ["customer_context_agent","service_scheduler_agent","qna_agent"]
# Our team supervisor is an LLM node. It just picks the next agent to process
# and decides when the work is completed
options = members + ["FINISH"]

system_prompt = (
    "You are a supervisor tasked with managing a conversation between the"
    f" following ai assistants: {members}. Given the following customer request,"
    " respond with the right ai assistant to act next. The assistant will perform a"
    " task and respond with its result and status. When you have determined that the query is answered,"
    " respond with FINISH."
    " \n\nCurrent customer service history is as follows:\n<service_history>\n{customer_info}\n</service_history>"
            "\nCurrent time: {time}."
)


class Router(TypedDict):
    """Worker to route to next. If no workers needed, route to FINISH."""

    # next: Literal[*options]
    next: Literal[*options]


def supervisor_node(state: MessagesState) -> Command[Literal[*members, "__end__"]]:
    messages = [
        {"role": "system", "content": system_prompt},
    ] + state["messages"]
    response = llm.with_structured_output(Router).invoke(messages)
    goto = response["next"]
    if goto == "FINISH":
        goto = END

    return Command(goto=goto)


service_scheduler_agent = create_react_agent(
    llm,
    tools=[db_tools.get_available_service_slots, db_tools.create_service_appointment_slot],
)

qna_agent = create_react_agent(
    llm,
    tools=[search_tools.perform_search_based_qna],
)

customer_context_agent = create_react_agent(
    llm,
    tools=[db_tools.fetch_customer_information],
)

def service_scheduler_node(state: MessagesState) -> Command[Literal["supervisor"]]:
    result = service_scheduler_agent.invoke(state)
    return Command(
        update={
            "messages": [
                HumanMessage(content=result["messages"][-1].content, name="service_scheduler_agent")
            ]
        },
        goto="supervisor",
    )

def qna_node(state: MessagesState) -> Command[Literal["supervisor"]]:
    result = qna_agent.invoke(state)
    return Command(
        update={
            "messages": [
                HumanMessage(content=result["messages"][-1].content, name="qna_agent")
            ]
        },
        goto="supervisor",
    )

def customer_context_node(state: MessagesState)-> Command[Literal["supervisor"]]:
    result = customer_context_agent.invoke(state)
    # print("??????result????",result)
    # return {"customer_info": db_tools.fetch_customer_information.invoke({})}
    return Command(
        update={
            "messages": [
                HumanMessage(content=result["messages"][-1].content, name="customer_context_agent")
            ]
        },
        goto="supervisor",
    )


builder = StateGraph(MessagesState)
builder.add_node("customer_context_agent", customer_context_node)
builder.add_edge(START, "customer_context_agent")
# builder.add_edge("fetch_customer_info", "supervisor")
builder.add_node("supervisor", supervisor_node)
builder.add_node("service_scheduler_agent", service_scheduler_node)
builder.add_node("qna_agent", qna_node)


# builder.add_edge("fetch_customer_info", "supervisor")
# for member in members:
#     # We want our workers to ALWAYS "report back" to the supervisor when done
#     builder.add_edge(member, "supervisor")


graph = builder.compile()



# graph_image = graph.get_graph().draw_mermaid_png()
# with open("graph_bot_app_v1.png", "wb") as f:
#     f.write(graph_bot_app_v1)
# display(Image("graph_bot_app_v1.png"))


def stream_graph_updates(user_input: str):
    events = graph.stream({"messages": [("user", user_input)]},config, subgraphs=True,stream_mode="values")
    l_events = list(events)
    msg = list(l_events[-1])
    r1 = msg[-1]['messages']
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
