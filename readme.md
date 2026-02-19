# Contoso Motocorp Service Bot

This repository contains the source code for the Contoso Motocorp Service Bot, implemented as a multi agent system using `langGraph`.
It helps customers schedule service appointments, provide feedback, and answer queries related to their vehicles.

## Project Structure

## Files and Directories

### Root Directory

- **.env**: Environment variables for database and API configurations.
- **.gitignore**: Specifies files and directories to be ignored by Git.
- **analyze_feedback.py**: Sample code to perform vector search on the Integrated Vector database - Azure SQL Database - to query based on user feedback
- **agent.py**: Hierarchical, multi agent system. Each Agent is powered by LLM and can make decisions. User communication happens directly
- **readme.md**: This file.
- **requirements.txt**: List of Python dependencies required for the project.

### Scripts Directory

- **analyze_feedback_sp.sql**: SQL script for the `AnalyzeFeedback` stored procedure.
- **capture-service-rating.sql**: SQL script for the `InsertServiceFeedback` stored procedure.
- **create_service_schedule_sp.sql**: SQL script for the `CreateServiceSchedule` stored procedure. 
- **db-create.sql**: SQL script to create and populate the database tables that can be used to run and demo this application

### Search data setup

The folder documents contains a PDF which is the user manual of Hero Honda HF100 Bike from the internet. Multimodal data extraction was used to extract insights from the text and images in this document, and the resulting enriched textual data is generated, and saved to file 'heromotocorp-sample-understood.md'
This could be uploaded to Azure AI Search, an index created and used in the application, as the basis on which customer queries on their product purchased can be answered

### Service Requests Directory

These are called by different Agents in the application

- **db_tools.py**: Contains database utility functions and tools. 
- **search_tools.py**: Contains tools for performing search-based Q&A.

## Setup

1. **Clone the repository**:
    ```sh
    git clone <repository-url>
    cd <repository-directory>
    ```

2. **Create a virtual environment**:
    ```sh
    python -m venv venv
    source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
    ```

3. **Install dependencies**:
    ```sh
    pip install -r requirements.txt
    ```

4. **Set up environment variables**:
    - Copy the [.env](http://_vscodecontentref_/13) file and update the values with your database and API credentials.

5. The .env file

```
AZURE_OPENAI_ENDPOINT="https://<>.openai.azure.com/"
AZURE_OPENAI_API_KEY=""
AZURE_OPENAI_DEPLOYMENT_NAME="gpt-4o"
AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT_NAME="text-embedding-ada-002"
API_VERSION="2023-08-01-preview"
API_TYPE="azure"
az_db_server = "<>.database.windows.net"
az_db_database = "<>"
az_db_username = "<>"
az_db_password = "<>"


ai_search_url = "https://<>.search.windows.net"
ai_search_key = "<>"
ai_index_name = "contoso-motocorp-index"
ai_semantic_config = "contoso-motocorp-config"
```

## Usage

### Running the Bot

To run the bot application, execute the following command:

```sh
python agent.py
```

### Analyzing Feedback
To analyze customer feedback, run:

```sh
python analyze_feedback.py
```

### Vector Search with Managed Identity

Use the new CLI flow to generate embeddings with Azure OpenAI and run vector similarity search in Azure SQL using managed identity.

1. Activate virtual environment:

```powershell
.\.venv\Scripts\Activate.ps1
```

2. Run vector search using the PowerShell wrapper:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\run-vector-search.ps1 -QueryText "timely delivery issue" -Top 10
```

3. Optional filters:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\run-vector-search.ps1 -QueryText "timely delivery issue" -MaxRating 3 -DistanceThreshold 0.5 -Top 10
```

4. Direct Python command (alternative):

```powershell
python scripts/vector_feedback_search.py --query-text "timely delivery issue" --max-rating 3 --distance-threshold 0.5 --top 10
```

Notes:
- Run commands exactly as plain text in terminal (do not include markdown link formats like `[file](url)`).
- The script reads configuration from `.env` and uses `DefaultAzureCredential` for both Azure OpenAI and Azure SQL.

### Database Setup

To set up the database, execute the SQL scripts in the scripts directory in the following order:

- db-create.sql: Creates and populates the database tables.
- create_service_schedule_sp.sql: Creates the CreateServiceSchedule stored procedure.
- capture-service-rating.sql: Creates the InsertServiceFeedback stored procedure.
- analyze_feedback_sp.sql: Creates the AnalyzeFeedback stored procedure.




### Tools and Utilities
#### Database Tools

- fetch_customer_information: Retrieves customer information from the database
- get_available_service_slots: Retrieves available service slots.
- create_service_appointment_slot: Creates a service appointment slot.
- store_service_feedback: Stores customer feedback for a service appointment.


### Graph comprising the Agents

agent.py -> Implements a full blown multi agent solution using `langGraph`. This has been adapted based on a similar sample in the Travel domain [here](https://langchain-ai.github.io/langgraph/tutorials/customer-support/customer-support/#conversation).Here the supervisor agents routes to the other agents, each of which are powered by an LLM and can make decisions of their own. They choose to use the different tools they have access to, can respond to the user directly and not have to go through the supervisor, when in a context in the conversation. Each of the Agents have a system prompt that determines their behavior and purpose.

![Contoso Motocorp Service Bot](graph_bot_app_v2.png)
