
from dotenv import load_dotenv
import os
import traceback
from langchain_core.tools import tool

from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient

load_dotenv()
ai_search_url = os.getenv("ai_search_url")
ai_index_name = os.getenv("ai_index_name")
ai_semantic_config = os.getenv("ai_semantic_config")

credential = DefaultAzureCredential()


sys_prompt= """
You are an AI Assistant tasked with creating a response to the user query. 

Ground rules
Be crisp in your responses to the user. If the information in the context is verbose, then format it using bullet points making for easy reading, without expunging key information at the same time.
** Always respond basis the information provided to you in the context. DO NOT MAKE STUFF UP**
** Be polite to the user always**
Empathise with the user when responding
"""

@tool
def perform_search_based_qna(query):
    """
    call this function to look up documentation and manuals to look for answers to the query posed by the Customer.

    """
    print("performing search based QnA")
    
    client = SearchClient(
        endpoint=ai_search_url,
        index_name=ai_index_name,
        credential=credential,
    )
    results = list(
        client.search(
            search_text=query,
            query_type="semantic",
            semantic_configuration_name=ai_semantic_config,
            top=5,
        )
    )
    return results
