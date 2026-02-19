"""
Streamlit UI for exploring Service_Feedback via vector search + rating filters.

Run with:
    streamlit run feedback_explorer.py
"""

import json
import os
import struct
from urllib import request as url_request

import pandas as pd
import pyodbc
import streamlit as st
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv()

SQL_SCOPE = "https://database.windows.net/.default"
OPENAI_SCOPE = "https://cognitiveservices.azure.com/.default"

# Rating columns in Service_Feedback
RATING_COLUMNS = {
    "rating_overall_experience": "Overall Experience",
    "rating_quality_of_work": "Quality of Work",
    "rating_timeliness": "Timeliness",
    "rating_politeness": "Politeness",
    "rating_cleanliness": "Cleanliness",
}


@st.cache_resource
def get_credential():
    return DefaultAzureCredential()


def get_sql_connection():
    credential = get_credential()
    server = os.getenv("az_db_server", "")
    database = os.getenv("az_db_database", "")
    token = credential.get_token(SQL_SCOPE).token
    token_bytes = token.encode("UTF-16-LE")
    token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)
    conn_str = (
        "Driver={ODBC Driver 18 for SQL Server};"
        f"SERVER={server};"
        f"DATABASE={database};"
        "Encrypt=yes;TrustServerCertificate=no;"
    )
    return pyodbc.connect(
        conn_str, attrs_before={1256: token_struct}
    )


def get_embedding(text: str) -> list[float]:
    credential = get_credential()
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
    deployment = os.getenv("AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT_NAME", "")
    api_version = os.getenv("AZURE_OPENAI_EMBEDDINGS_API_VERSION", "2023-05-15")
    token = credential.get_token(OPENAI_SCOPE).token

    url = f"{endpoint}/openai/deployments/{deployment}/embeddings?api-version={api_version}"
    body = json.dumps({"input": text}).encode("utf-8")
    req = url_request.Request(
        url=url,
        method="POST",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with url_request.urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload["data"][0]["embedding"]


def build_query(
    rating_filters: dict[str, int | None],
    distance_threshold: float | None,
    top_n: int,
) -> str:
    """Build the T-SQL query string for display."""
    lines = [
        "DECLARE @embedding_json nvarchar(max) = <embedding from query text>;",
        "DECLARE @e vector(1536) = CAST(@embedding_json AS vector(1536));",
        "",
        f"SELECT TOP ({top_n})",
    ]
    select_cols = [
        "    sf.feedback_id",
        "    sf.customer_id",
        "    sf.schedule_id",
        "    sf.feedback_text",
        "    sf.rating_quality_of_work",
        "    sf.rating_timeliness",
        "    sf.rating_politeness",
        "    sf.rating_cleanliness",
        "    sf.rating_overall_experience",
        "    sf.feedback_date",
        "    vector_distance('cosine', @e, sf.feedback_vector) AS distance",
    ]
    lines.append(",\n".join(select_cols))
    lines.append("FROM Service_Feedback sf")

    where_clauses = ["sf.feedback_vector IS NOT NULL"]
    if distance_threshold is not None:
        where_clauses.append(
            f"vector_distance('cosine', @e, sf.feedback_vector) <= {distance_threshold}"
        )

    for col, max_val in rating_filters.items():
        if max_val is not None:
            where_clauses.append(f"sf.{col} <= {max_val}")

    lines.append("WHERE")
    lines.append("    " + "\n    AND ".join(where_clauses))
    lines.append("ORDER BY distance;")

    return "\n".join(lines)


def execute_query(
    embedding: list[float],
    rating_filters: dict[str, int | None],
    distance_threshold: float | None,
    top_n: int,
) -> pd.DataFrame:
    """Execute the parameterized vector search query and return a DataFrame."""
    parts = []
    params = []

    parts.append(
        "DECLARE @embedding_json nvarchar(max) = ?;\n"
        "DECLARE @e vector(1536) = CAST(@embedding_json AS vector(1536));\n"
    )
    params.append(json.dumps(embedding))

    parts.append(f"SELECT TOP ({top_n})")
    select_cols = [
        "    sf.feedback_id",
        "    sf.customer_id",
        "    sf.schedule_id",
        "    sf.feedback_text",
        "    sf.rating_quality_of_work",
        "    sf.rating_timeliness",
        "    sf.rating_politeness",
        "    sf.rating_cleanliness",
        "    sf.rating_overall_experience",
        "    sf.feedback_date",
        "    vector_distance('cosine', @e, sf.feedback_vector) AS distance",
    ]
    parts.append(",\n".join(select_cols))
    parts.append("FROM Service_Feedback sf")

    where_clauses = ["sf.feedback_vector IS NOT NULL"]
    if distance_threshold is not None:
        where_clauses.append(
            f"vector_distance('cosine', @e, sf.feedback_vector) <= {distance_threshold}"
        )

    for col, max_val in rating_filters.items():
        if max_val is not None:
            where_clauses.append(f"sf.{col} <= ?")
            params.append(max_val)

    parts.append("WHERE")
    parts.append("    " + "\n    AND ".join(where_clauses))
    parts.append("ORDER BY distance;")

    sql = "\n".join(parts)

    conn = get_sql_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        cursor.close()
    finally:
        conn.close()

    return pd.DataFrame.from_records(rows, columns=columns)


# ── Streamlit UI ──────────────────────────────────────────────

st.set_page_config(page_title="Feedback Explorer", layout="wide")
st.title("Contoso Motocorp — Feedback Explorer")
st.markdown("Explore service feedback using **vector similarity search** and **rating filters**.")

# Sidebar controls
with st.sidebar:
    st.header("Search Parameters")

    sentiment_text = st.text_area(
        "Vector Search Query",
        placeholder="e.g. The customer was unhappy with cleanliness",
        help="Natural language text — converted to an embedding via Azure OpenAI and used for cosine vector similarity search against feedback_vector.",
    )

    st.subheader("Rating Filters (max)")
    st.caption("Only include feedback where the rating is ≤ the selected value. Drag to 5 to disable a filter.")
    rating_filters: dict[str, int | None] = {}
    for col, label in RATING_COLUMNS.items():
        val = st.slider(label, min_value=1, max_value=5, value=5, key=col)
        rating_filters[col] = val if val < 5 else None

    st.subheader("Vector Distance")
    distance_threshold = st.slider(
        "Max cosine distance",
        min_value=0.0,
        max_value=1.0,
        value=0.5,
        step=0.05,
        help="Filter results to only those within this cosine distance from the query embedding.",
    )
    top_n = st.slider("Top N results", min_value=1, max_value=100, value=20)

    run_button = st.button("Run Query", type="primary", use_container_width=True)

# Main area
if run_button:
    if not sentiment_text.strip():
        st.warning("Please enter a query text for vector search.")
    else:
        # Build display SQL
        display_sql = build_query(
            rating_filters=rating_filters,
            distance_threshold=distance_threshold,
            top_n=top_n,
        )

        st.subheader("Generated T-SQL")
        st.code(display_sql, language="sql")

        # Execute
        with st.spinner("Generating embedding from Azure OpenAI..."):
            embedding = get_embedding(sentiment_text.strip())

        with st.spinner("Running vector search on Azure SQL..."):
            df = execute_query(
                embedding=embedding,
                rating_filters=rating_filters,
                distance_threshold=distance_threshold,
                top_n=top_n,
            )

        st.subheader(f"Results ({len(df)} rows)")
        if df.empty:
            st.info("No matching feedback found. Try adjusting your filters or increasing the distance threshold.")
        else:
            st.dataframe(df, use_container_width=True, hide_index=True)
else:
    st.info("Enter a search query in the sidebar and click **Run Query** to perform a vector similarity search.")
