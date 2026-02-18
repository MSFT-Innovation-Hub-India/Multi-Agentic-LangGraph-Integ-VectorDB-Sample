import pyodbc
import struct
from dotenv import load_dotenv
import os
import requests
import json
from azure.identity import DefaultAzureCredential

load_dotenv()

az_db_server = os.getenv("az_db_server")
az_db_database = os.getenv("az_db_database")

az_openai_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
az_openai_deployment_name = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
az_openai_embedding_deployment_name = os.getenv(
    "AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT_NAME"
)
az_api_type = os.getenv("API_TYPE")
az_openai_version = os.getenv("API_VERSION")

credential = DefaultAzureCredential()


def get_sql_connection():
    """Create a pyodbc connection to Azure SQL using DefaultAzureCredential."""
    token = credential.get_token("https://database.windows.net/.default").token
    token_bytes = token.encode("UTF-16-LE")
    token_struct = struct.pack(f'<I{len(token_bytes)}s', len(token_bytes), token_bytes)
    SQL_COPT_SS_ACCESS_TOKEN = 1256
    conn_str = (
        f"Driver={{ODBC Driver 18 for SQL Server}};"
        f"SERVER={az_db_server};"
        f"DATABASE={az_db_database}"
    )
    return pyodbc.connect(conn_str, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct})



def get_embedding(text):
    token = credential.get_token("https://cognitiveservices.azure.com/.default").token
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    url = f"{az_openai_endpoint}openai/deployments/{az_openai_embedding_deployment_name}/embeddings?api-version=2023-05-15"
    print("the url is ", url)
    payload = {"input": text}
    response = requests.post(url, headers=headers, json=payload)

    if response.status_code == 200:
        embed_content = response.json()["data"][0]["embedding"]
        # print("Embedding content\n", embed_content, "\n")
        print("retrieved embedding content")
        return embed_content
    else:
        print(f"Error fetching embedding: {response.status_code} - {response.text}")
        raise Exception(
            f"Error fetching embedding: {response.status_code} - {response.text}"
        )


def run_analyze_feedback():
    connection = get_sql_connection()
    cursor = connection.cursor()
    
    sentiment_query = "The customer was displeased with the overall service"
    v_query = json.dumps(json.loads(str(get_embedding(sentiment_query)))),
    
    # Call the stored procedure
    stored_procedure = """
    EXEC AnalyzeFeedback ?
    """
    cursor.execute(
        stored_procedure,
        (
            v_query
        ),
    )
    rows = cursor.fetchall()
    print(rows)



    # print('database call response has been parsed')
    cursor.close()
    connection.close()
    

"""
The following function does not work yet. Do not use it
This function runs a dynamic SQL query that uses the vector_distance function to calculate the cosine distance between the input vector and the feedback_vector column in the Service_Feedback table. The query returns the feedback_text and distance columns for feedback with a rating_overall_experience of 3 or less and a cosine distance of less than 0.5."""
def run_dynamic_sql():
    connection = get_sql_connection()
    cursor = connection.cursor()
    
    sentiment_query = "The customer was displeased with the overall service"
    v_query = "N'"+json.dumps(json.loads(str(get_embedding(sentiment_query))))+"'"
    # v_query = json.dumps(json.loads(str(get_embedding(sentiment_query))))

    
    
    
    dynamic_sql_query = f"""
    drop table if exists #query_vectors
	create table #query_vectors
	(
		id int not null identity primary key,
		vector vector(1536) not null
	);
		insert into #query_vectors (vector)
	select 
		cast(a as vector(1536))
	from
		( values 
			({v_query})
		) V(a)
	;
	declare @v1 vector(1536) = (SELECT vector FROM #query_vectors WHERE id = 1)
	select 
    sf.feedback_text,    
    vector_distance('cosine', @v1, sf.feedback_vector) as distance
    from
        Service_Feedback sf
    where
        vector_distance('cosine', @v1, sf.feedback_vector) < 0.5
        and sf.rating_overall_experience <=3
    order by
        distance
    """

    print(dynamic_sql_query)
    
    # Call the stored procedure
    stored_procedure_call = """
    EXEC ExecuteDynamicSQL @sqlCommand = ?
    """
    cursor.execute(
        stored_procedure_call,dynamic_sql_query
    )
    cursor.commit()
    rows = cursor.fetchall()
    print(rows)



    # print('database call response has been parsed')
    cursor.close()
    connection.close()


def run_feedback_vector_query():
    connection = get_sql_connection()
    cursor = connection.cursor()
    
    sentiment_query = "The customer was displeased with the overall service"
    v_query = json.dumps(json.loads(str(get_embedding(sentiment_query)))),
    
    t_sql = ""
    
    # Call the stored procedure
    stored_procedure = """
    EXEC AnalyzeFeedback ?
    """
    cursor.execute(
        stored_procedure,
        (
            v_query
        ),
    )
    rows = cursor.fetchall()
    print(rows)



    # print('database call response has been parsed')
    cursor.close()
    connection.close()


# call this function to run the code
run_analyze_feedback()
# run_dynamic_sql()