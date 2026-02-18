import pyodbc
import struct
from dotenv import load_dotenv
import os
import requests
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
import json
import traceback
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


@tool
def fetch_customer_information(config: RunnableConfig) -> list[dict]:
    """
    For an input customer name, retrieves all information about a customer from the database, like the vehicle details and service schedules.

    """
    print(
        "******** initializing customer information from the database.***************"
    )
    configuration = config.get("configurable", {})
    customer_name = configuration.get("customer_name", None)
    if not customer_name:
        raise ValueError("No customer Name configured.")

    connection = get_sql_connection()
    cursor = connection.cursor()
    query = """
    SELECT
        c.customer_id AS CustomerID,
        c.name AS CustomerName,
        v.vehicle_id AS VehicleID,
        v.model AS Model,
        v.year AS YearOfManufacture,
        v.registration_number AS RegistrationNumber,
        ss.schedule_id AS ScheduleID,
        ss.service_date AS ServiceDate,
        ss.start_time AS StartTime,
        ss.end_time AS EndTime,
        ss.status AS ScheduleStatus
    FROM
        Customers c
        INNER JOIN Vehicles v ON c.customer_id = v.customer_id
        LEFT JOIN Service_Schedules ss ON v.vehicle_id = ss.vehicle_id
    WHERE
        c.name = ?;
    """
    cursor.execute(query, (customer_name,))
    # print('database call completeed without errors')
    rows = cursor.fetchall()
    column_names = [column[0] for column in cursor.description]
    results = [dict(zip(column_names, row)) for row in rows]
    response_message = ""
    # print the results
    for result in results:
        response_message += f"Customer ID: {result['CustomerID']}\n"
        response_message += f"Customer Name: {result['CustomerName']}\n"
        response_message += f"Vehicle ID: {result['VehicleID']}\n"
        response_message += f"Model: {result['Model']}\n"
        response_message += f"Year of Manufacture: {result['YearOfManufacture']}\n"
        response_message += f"Registration Number: {result['RegistrationNumber']}\n"
        response_message += f"Schedule ID: {result['ScheduleID']}\n"
        response_message += f"Service Date: {result['ServiceDate']}\n"
        response_message += f"Start Time: {result['StartTime']}\n"
        response_message += f"End Time: {result['EndTime']}\n"
        response_message += f"Schedule Status: {result['ScheduleStatus']}\n\n"

    # print('database call response has been parsed')
    cursor.close()
    connection.close()
    return response_message


@tool
def get_available_service_slots(start_date):
    """
    For an input start date, retrieves all available service schedule slots.

    """
    response_message = ""
    connection = get_sql_connection()
    cursor = connection.cursor()
    query = """
    
    WITH PotentialSlots AS (
        SELECT
            DATEADD(MINUTE, (t.N * 60 * 24) + s.SlotOffset, CAST(? AS DATETIME)) AS SlotStart,
            DATEADD(MINUTE, (t.N * 60 * 24) + s.SlotOffset + 60, CAST(? AS DATETIME)) AS SlotEnd
        FROM
            (VALUES (0), (1), (2)) AS t(N) -- Days: 0 = Monday, 1 = Tuesday, 2 = Wednesday
        CROSS JOIN
            (VALUES
                (9 * 60), (10 * 60), (11 * 60), (12 * 60), -- Morning slots
                (14 * 60), (15 * 60), (16 * 60), (17 * 60) -- Afternoon slots
            ) AS s(SlotOffset)
    ),
    BookedSlots AS (
        SELECT
            CAST(service_date AS DATETIME) + CAST(start_time AS DATETIME) AS SlotStart,
            CAST(service_date AS DATETIME) + CAST(end_time AS DATETIME) AS SlotEnd
        FROM
            Service_Schedules
        WHERE
            service_date BETWEEN ? AND DATEADD(DAY, 2, ?)
            AND status = 'Scheduled'
    )
    SELECT DISTINCT
        ps.SlotStart AS AvailableStart,
        ps.SlotEnd AS AvailableEnd
    FROM
        PotentialSlots ps
    LEFT JOIN
        BookedSlots bs ON ps.SlotStart < bs.SlotEnd AND ps.SlotEnd > bs.SlotStart
    WHERE
        bs.SlotStart IS NULL
    ORDER BY
        ps.SlotStart;
    """
    cursor.execute(query, (start_date, start_date, start_date, start_date))
    rows = cursor.fetchall()
    column_names = [column[0] for column in cursor.description]
    results = [dict(zip(column_names, row)) for row in rows]
    cursor.close()
    connection.close()
    return results


def create_service_appointment_slot(start_date_time, vehicle_id=1, service_type_id=1):
    """
    For an input start date time, vehicle_id and service_type_id , register the service appointment slot for the Customer.

    """
    response_message = ""
    connection = get_sql_connection()
    cursor = connection.cursor()

    try:
        # Calling the stored procedure
        cursor.execute(
            """
                EXEC CreateServiceSchedule @SelectedSlotStart = ?, @VehicleID = ?, @ServiceTypeID = ?
            """,
            (start_date_time, vehicle_id, service_type_id),
        )

        # Fetching the results
        rows = cursor.fetchall()
        if rows:
            for row in rows:
                print(row)
        else:
            print("No results returned.")

        # Commit the transaction if necessary
        connection.commit()

        response_message = (
            "Service appointment slot created successfully for the slot start datetime: "
            + start_date_time
        )
        return response_message
    except Exception as e:
        print(f"Error creating the Service appointment: {e}")
        return None
    finally:
        cursor.close()
        connection.close()


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


# Function to convert a list of floats into a list of single-item tuples
def convert_to_tvp_format(vector):
    return [(value,) for value in vector]


@tool
def store_service_feedback(
    schedule_id,
    customer_id,
    feedback_text,
    rating_quality_of_work,
    rating_timeliness,
    rating_politeness,
    rating_cleanliness,
    rating_overall_experience,
    feedback_date,
):
    """
    Capture the service feedback of the customer for the service appointment slot.

    """
    print(
        "capturing the service feedback of the customer for the service appointment slot."
    )
    print(
        f"schedule_id: {schedule_id}, customer_id: {customer_id}, feedback_text: {feedback_text}, rating_quality_of_work: {rating_quality_of_work}, rating_timeliness: {rating_timeliness}, rating_politeness: {rating_politeness}, rating_cleanliness: {rating_cleanliness},  rating_overall_experience: {rating_overall_experience}, feedback_date: {feedback_date}"
    )

    try:
        # print('0')
        # v_feedback_text = "'"+str(get_embedding(feedback_text))+"'"
        # v_feedback_text = my_embeddingfv

        connection = get_sql_connection()
        cursor = connection.cursor()

        # Call the stored procedure
        stored_procedure = """
        EXEC InsertServiceFeedback ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        """
        cursor.execute(
            stored_procedure,
            (
                schedule_id,
                customer_id,
                feedback_text,
                json.dumps(json.loads(str(get_embedding(feedback_text)))),
                rating_quality_of_work,
                rating_timeliness,
                rating_politeness,
                rating_cleanliness,
                rating_overall_experience,
                feedback_date,
            ),
        )
        connection.commit()
        print("Feedback inserted successfully.")
        response_message = (
            "Service feedback captured successfully for the schedule_id: " + str(schedule_id)
        )

    except Exception as e:
        print(f"************************Error: {e}*********************************")
        #print stack trace

        traceback.print_exc()
        response_message = "Error capturing the service feedback."

    finally:
        cursor.close()
        connection.close()
        return response_message
