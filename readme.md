# Contoso Motocorp Service Bot

A multi-agent customer service assistant for a motorcycle dealership, built with [LangGraph](https://langchain-ai.github.io/langgraph/), Azure OpenAI, Azure SQL Database (with native vector support), and Azure AI Search.

The bot helps customers schedule vehicle service appointments, provide feedback on completed services, and get answers to product-related questions — all through natural conversation.

## Architecture

The application implements a **hierarchical multi-agent system** using LangGraph, adapted from the [LangGraph customer support tutorial](https://langchain-ai.github.io/langgraph/tutorials/customer-support/customer-support/#conversation). A primary supervisor agent routes conversations to specialized agents, each powered by its own LLM and system prompt:

| Agent | Purpose | Tools |
|---|---|---|
| **Primary Assistant** | Greets the user, understands intent, and delegates to the right specialist | Routes to sub-agents |
| **Service Scheduler** | Handles appointment booking and slot queries | `get_available_service_slots`, `create_service_appointment_slot` |
| **Service Feedback** | Captures post-service ratings and comments | `store_service_feedback` |
| **Search Q&A** | Answers product/vehicle questions using Azure AI Search | `perform_search_based_qna` |

Each agent can respond directly to the user without routing back through the supervisor, and can escalate back when the conversation context changes.

![Agent Graph](graph_bot_app_v2.png)

### System Overview

Both user-facing applications share the same Azure back-end services and authenticate exclusively through `DefaultAzureCredential` (no API keys or passwords).

```mermaid
flowchart TB
    subgraph Clients["User Interfaces"]
        C["Customer - CLI Terminal"]
        S["Back-Office Staff - Web Browser"]
    end

    subgraph Apps["Applications"]
        A["agent.py - Multi-Agent Service Bot"]
        F["feedback_explorer.py - Streamlit Dashboard"]
    end

    subgraph LG["LangGraph Agent Graph"]
        PA["Primary Assistant - Supervisor / Router"]
        SS["Service Scheduling Agent"]
        SF["Service Feedback Agent"]
        SQ["Search QnA Agent"]
        PA -- "ToServiceScheduler" --> SS
        PA -- "ToServiceFeedback" --> SF
        PA -- "ToSearchQnA" --> SQ
    end

    subgraph AZ["Azure Services"]
        AAD["Microsoft Entra ID - DefaultAzureCredential"]
        GPT["Azure OpenAI - GPT-4o Chat"]
        EMB["Azure OpenAI - ada-002 Embeddings"]
        DB[("Azure SQL Database - vector 1536-dim")]
        AIS["Azure AI Search - Semantic"]
    end

    C --> A
    A --> LG
    LG -- "Chat completions" --> GPT
    SS -- "Slot queries + appointment creation" --> DB
    SF -- "Store feedback + vector" --> DB
    SF -- "Embed feedback text" --> EMB
    SQ -- "Semantic search" --> AIS

    S --> F
    F -- "Embed query text" --> EMB
    F -- "vector_distance cosine search" --> DB

    A -. "auth" .-> AAD
    F -. "auth" .-> AAD
```

### Customer-Facing Bot — Request / Response Flow

Each conversation turn flows through the LangGraph state graph. On the **first turn**, customer data is fetched from Azure SQL and cached in state; subsequent turns reuse the cache. The Primary Assistant classifies user intent via GPT-4o tool binding and delegates to the matching sub-agent. Sub-agents maintain multi-turn dialog via a `dialog_state` stack and a `MemorySaver` checkpointer keyed by `thread_id`.

```mermaid
sequenceDiagram
    actor Customer
    participant Bot as agent.py - LangGraph
    participant PA as Primary Assistant
    participant Sub as Sub-Agent
    participant GPT as Azure OpenAI GPT-4o
    participant EMB as Azure OpenAI ada-002
    participant SQL as Azure SQL Database
    participant AIS as Azure AI Search
    participant Auth as Entra ID

    Note over Bot,Auth: Application Startup
    Bot->>Auth: DefaultAzureCredential
    Auth-->>Bot: Credential + token provider
    Bot->>GPT: Initialize AzureChatOpenAI
    Bot->>Bot: Compile StateGraph + MemorySaver
    Bot->>Customer: Print greeting + capability list

    Note over Customer,Auth: Conversation Turn - repeated per message
    Customer->>Bot: Natural language message
    Bot->>Bot: graph.stream with messages and config

    Note over Bot,SQL: fetch_customer_info node
    Bot->>Auth: get_token for database.windows.net
    Auth-->>Bot: SQL access token
    Bot->>SQL: SELECT Customers JOIN Vehicles JOIN Service_Schedules WHERE name = ?
    SQL-->>Bot: Customer profile + vehicle + history
    Note right of Bot: Cached after first turn

    Note over Bot,PA: route_to_workflow - check dialog_state
    Bot->>PA: Invoke with system prompt + customer_info + messages
    PA->>GPT: Chat completion with tool bindings
    GPT-->>PA: Intent classification - tool_call or direct reply

    alt Service Scheduling
        PA->>Bot: tool_call ToServiceScheduler
        Bot->>Sub: enter_service_scheduling - set dialog_state, inject ToolMessage
        Sub->>GPT: Chat completion with scheduling system prompt
        GPT-->>Sub: tool_call get_available_service_slots
        Sub->>Auth: get_token for database.windows.net
        Sub->>SQL: CTE query - PotentialSlots minus BookedSlots for date range
        SQL-->>Sub: Available 1-hour time slots
        Sub->>GPT: Format results for customer
        GPT-->>Sub: Slot list in natural language
        Sub-->>Customer: Here are the available slots

        Note over Customer,Sub: Customer selects a slot - next turn
        Customer->>Bot: Book the 2 PM slot on Monday
        Bot->>Bot: graph.stream - fetch_customer_info cached
        Bot->>Sub: route_to_workflow - service_scheduling active
        Sub->>GPT: Chat completion
        GPT-->>Sub: tool_call create_service_appointment_slot
        Sub->>SQL: EXEC CreateServiceSchedule @SlotStart, @VehicleID, @ServiceTypeID
        SQL-->>Sub: Appointment created
        Sub->>GPT: Generate confirmation message
        GPT-->>Sub: CompleteOrEscalate - task done
        Sub->>Bot: leave_skill - pop dialog_state
        Bot->>PA: Control returns to Primary Assistant

    else Service Feedback
        PA->>Bot: tool_call ToServiceFeedback
        Bot->>Sub: enter_service_feedback - set dialog_state

        loop Multi-turn feedback collection
            Sub->>GPT: Chat completion with feedback system prompt
            GPT-->>Sub: Ask for rating or specific aspect
            Sub-->>Customer: Please rate overall experience 1-5
            Customer->>Bot: Provides rating + comments
            Bot->>Sub: Resume in service_feedback state
        end

        Sub->>GPT: All feedback collected
        GPT-->>Sub: tool_call store_service_feedback
        Sub->>Auth: get_token for cognitiveservices
        Sub->>EMB: POST /embeddings with feedback_text
        EMB-->>Sub: 1536-dimension embedding vector
        Sub->>Auth: get_token for database.windows.net
        Sub->>SQL: EXEC InsertServiceFeedback with feedback + embedding + 5 ratings
        SQL-->>Sub: Feedback + vector stored
        Sub->>GPT: Generate thank-you message
        GPT-->>Sub: CompleteOrEscalate
        Sub->>Bot: leave_skill - pop dialog_state
        Bot->>PA: Control returns to Primary Assistant

    else Product QnA
        PA->>Bot: tool_call ToSearchQnA
        Bot->>Sub: enter_search_qna - set dialog_state
        Sub->>GPT: Chat completion with search system prompt
        GPT-->>Sub: tool_call perform_search_based_qna
        Sub->>Auth: DefaultAzureCredential
        Sub->>AIS: Semantic search - index contoso-motocorp-index, top 5
        AIS-->>Sub: Top 5 ranked document chunks
        Sub->>GPT: Chat completion with search context
        GPT-->>Sub: Grounded natural language answer
        Sub-->>Customer: Answer from product documentation
    end
```

### Back-Office Feedback Explorer — Request / Response Flow

The Streamlit dashboard converts a natural-language query into an embedding vector via Azure OpenAI, then runs a parameterized `vector_distance` cosine-similarity query against Azure SQL, combining semantic search with structured rating filters. The generated T-SQL is displayed alongside the results for transparency.

```mermaid
sequenceDiagram
    actor Staff as Back-Office Staff
    participant Browser as Web Browser
    participant ST as Streamlit - feedback_explorer.py
    participant Auth as Entra ID
    participant EMB as Azure OpenAI ada-002
    participant SQL as Azure SQL Database

    Note over Staff,SQL: Application Launch
    Staff->>Browser: streamlit run feedback_explorer.py
    Browser->>ST: HTTP request
    ST->>Browser: Render page title + sidebar controls

    Note over Staff,SQL: Configure Search Parameters
    Staff->>Browser: Enter vector search query, e.g. unhappy with cleanliness
    Staff->>Browser: Adjust 5 rating sliders - Overall, Quality, Timeliness, Politeness, Cleanliness
    Staff->>Browser: Set cosine distance threshold 0.0 to 1.0
    Staff->>Browser: Set Top N results 1 to 100
    Staff->>Browser: Click Run Query
    Browser->>ST: Submit parameters

    Note over ST,ST: T-SQL Generation
    ST->>ST: build_query - generate display T-SQL with vector_distance + rating WHERE clauses
    ST->>Browser: Show generated T-SQL

    Note over ST,EMB: Embedding Generation
    ST->>Auth: get_token for cognitiveservices
    Auth-->>ST: Bearer token
    ST->>EMB: POST /openai/deployments/ada-002/embeddings with query text
    EMB-->>ST: 1536-dimension embedding vector

    Note over ST,SQL: Parameterized Vector Search
    ST->>Auth: get_token for database.windows.net
    Auth-->>ST: SQL access token via pyodbc attr 1256
    ST->>SQL: DECLARE @e vector = CAST ? AS vector. SELECT TOP N feedback + distance FROM Service_Feedback WHERE distance and ratings filtered. ORDER BY distance.
    SQL-->>ST: Matching feedback rows + cosine distances

    Note over ST,Browser: Results Display
    ST->>ST: Convert rows to pandas DataFrame
    ST->>Browser: Render result count header
    ST->>Browser: Render interactive data table - sortable, scrollable, full-width
    Browser->>Staff: Browse feedback with similarity scores
```

### Why an Integrated Vector Database Matters

The `Service_Feedback` table in Azure SQL Database stores **three kinds of data in a single row**:

| Column type | Examples | Purpose |
|---|---|---|
| **Structured ratings** | `rating_overall_experience`, `rating_quality_of_work`, `rating_timeliness`, `rating_politeness`, `rating_cleanliness` (integers 1–5) | Quantitative scoring, aggregation, filtering |
| **Free-form text** | `feedback_text` (nvarchar) | Verbatim customer comments |
| **Vector embedding** | `feedback_vector` (vector(1536)) | Semantic similarity search via `vector_distance('cosine', ...)` |

Because all three live in the same table, the Feedback Explorer can issue **a single T-SQL query** that:

1. **Semantically searches** free-form feedback text by comparing the query embedding against `feedback_vector` using cosine distance.
2. **Filters on structured ratings** (e.g., `rating_cleanliness <= 2`) using standard `WHERE` clauses.
3. **Joins, groups, and counts** across both dimensions — for example, counting how many low-cleanliness-rated feedbacks are semantically similar to "vehicle returned dirty."

In a traditional architecture this would require **multiple disparate systems** — a relational database for ratings, a separate vector store for embeddings, and an application layer to reconcile results across both. With Azure SQL's native vector support, the operational data, free-text feedback, and vector embeddings coexist in one engine, eliminating cross-system ETL, consistency issues, and reconciliation logic. One query, one transaction boundary, one source of truth.

## Project Structure

```
├── agent.py                     # Main multi-agent bot application
├── feedback_explorer.py         # Streamlit UI for interactive feedback analysis
├── requirements.txt             # Python dependencies
├── .env                         # Environment variables (not checked in)
├── .gitignore
├── documents/
│   ├── hf_100_aug_2024.pdf      # Hero Honda HF100 user manual (source)
│   └── heromotocorp-sample-understood.md  # Extracted & enriched content for search index
├── scripts/
│   ├── db-create.sql            # Database table creation & seed data
│   ├── create_service_schedule_sp.sql  # CreateServiceSchedule stored procedure
│   ├── capture-service-rating.sql      # InsertServiceFeedback stored procedure
│   ├── analyze_feedback_sp.sql         # AnalyzeFeedback stored procedure
│   ├── get_embeddings_sp.sql           # Embedding generation stored procedure
│   └── vector_feedback_search.py       # CLI vector search script
└── service_requests/
    ├── db_tools.py              # Database tools used by agents
    └── search_tools.py          # Azure AI Search tools used by agents
```

## Prerequisites

- Python 3.12+
- Azure subscription with:
  - **Azure OpenAI** (GPT-4o deployment + text-embedding-ada-002)
  - **Azure SQL Database** with [native vector support](https://learn.microsoft.com/azure/azure-sql/database/ai-artificial-intelligence-intelligent-applications?view=azuresql)
  - **Azure AI Search** (for product Q&A)
- ODBC Driver 18 for SQL Server
- Authentication via `DefaultAzureCredential` (Azure CLI, managed identity, etc.)

## Setup

1. **Clone the repository**:

    ```sh
    git clone <repository-url>
    cd Multi-Agentic-LangGraph-Integ-VectorDB-Sample
    ```

2. **Create and activate a virtual environment**:

    ```sh
    python -m venv .venv
    .venv\Scripts\Activate.ps1   # Windows PowerShell
    # or: source .venv/bin/activate  # macOS/Linux
    ```

3. **Install dependencies**:

    ```sh
    pip install -r requirements.txt
    ```

4. **Configure environment variables** — create a `.env` file in the project root:

    ```
    AZURE_OPENAI_ENDPOINT="https://<your-openai>.openai.azure.com/"
    AZURE_OPENAI_DEPLOYMENT_NAME="gpt-4o"
    AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT_NAME="text-embedding-ada-002"
    API_VERSION="2023-08-01-preview"
    API_TYPE="azure"

    az_db_server="<your-server>.database.windows.net"
    az_db_database="<your-database>"

    ai_search_url="https://<your-search>.search.windows.net"
    ai_index_name="contoso-motocorp-index"
    ai_semantic_config="contoso-motocorp-config"
    ```

    > Authentication uses `DefaultAzureCredential` — no API keys or database passwords needed. Ensure your identity has the required RBAC roles on Azure OpenAI, Azure SQL, and Azure AI Search.

5. **Set up the database** — run the SQL scripts in order:

    1. `scripts/db-create.sql` — creates tables and inserts seed data
    2. `scripts/create_service_schedule_sp.sql` — creates the `CreateServiceSchedule` procedure
    3. `scripts/capture-service-rating.sql` — creates the `InsertServiceFeedback` procedure
    4. `scripts/analyze_feedback_sp.sql` — creates the `AnalyzeFeedback` procedure
    5. `scripts/get_embeddings_sp.sql` — creates the embedding generation procedure (**update the hardcoded Azure OpenAI endpoint URL** inside the procedure body to match your deployment)

6. **Set up Azure AI Search** — upload the contents of `documents/heromotocorp-sample-understood.md` to an Azure AI Search index named `contoso-motocorp-index` with a semantic configuration named `contoso-motocorp-config`.

## Usage

This project has two distinct applications for two different user personas:

---

### 1. Customer-Facing Service Bot

**Audience:** Customers interacting with Contoso Motocorp for vehicle services.

The multi-agent conversational bot allows customers to:
- Schedule vehicle service appointments
- Provide feedback and ratings on completed services
- Ask product and vehicle-related questions

**Launch:**

```sh
python agent.py
```

The bot greets the customer by name, introduces itself, and lists the available capabilities. Customers type their requests in natural language — the supervisor agent routes to the appropriate specialist agent automatically.

---

### 2. Back-Office Feedback Explorer

**Audience:** Back-office / operations staff analyzing customer sentiment and service quality.

A Streamlit-based interactive dashboard for exploring customer feedback stored in the Azure SQL vector database. Staff can combine semantic vector search with rating filters to identify trends, problem areas, and low-satisfaction patterns.

**Launch:**

```sh
streamlit run feedback_explorer.py
```

This opens a browser-based UI with:
- **Vector search** — enter a natural language sentiment query (e.g., "unhappy with cleanliness"), which gets embedded via Azure OpenAI and searched against the `feedback_vector` column using cosine distance
- **Rating sliders** — filter by max rating across 5 dimensions (overall experience, quality of work, timeliness, politeness, cleanliness)
- **Distance threshold** — control how similar results must be to the query
- **Generated T-SQL** — view the exact query being executed against Azure SQL
- **Results grid** — browse matching feedback in an interactive data table

---

### Additional CLI Tools

An alternative command-line script for feedback analysis — useful for scripting or quick one-off queries.

**Vector search with filters:**

```sh
python scripts/vector_feedback_search.py --query-text "poor service quality" --max-rating 3 --distance-threshold 0.5 --top 10
```
