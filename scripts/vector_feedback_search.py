import argparse
import json
import os
import struct
from typing import Any
from urllib import request

import pyodbc
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv


SQL_COPT_SS_ACCESS_TOKEN = 1256
SQL_SCOPE = "https://database.windows.net/.default"
OPENAI_SCOPE = "https://cognitiveservices.azure.com/.default"


def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value.strip().strip('"')


def get_sql_connection(credential: DefaultAzureCredential, server: str, database: str) -> pyodbc.Connection:
    token = credential.get_token(SQL_SCOPE).token
    token_bytes = token.encode("UTF-16-LE")
    token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)

    conn_str = (
        "Driver={ODBC Driver 18 for SQL Server};"
        f"SERVER={server};"
        f"DATABASE={database};"
        "Encrypt=yes;TrustServerCertificate=no;"
    )
    return pyodbc.connect(conn_str, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct})


def get_embedding(
    credential: DefaultAzureCredential,
    endpoint: str,
    deployment: str,
    text: str,
    api_version: str,
) -> list[float]:
    token = credential.get_token(OPENAI_SCOPE).token
    clean_endpoint = endpoint.rstrip("/")
    url = (
        f"{clean_endpoint}/openai/deployments/{deployment}/embeddings"
        f"?api-version={api_version}"
    )

    body = json.dumps({"input": text}).encode("utf-8")
    req = request.Request(
        url=url,
        method="POST",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )

    with request.urlopen(req, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))

    data = payload.get("data", [])
    if not data or "embedding" not in data[0]:
        raise RuntimeError(f"Unexpected embedding response: {payload}")

    return data[0]["embedding"]


def run_vector_query(
    connection: pyodbc.Connection,
    embedding: list[float],
    top: int,
    max_rating: int | None,
    distance_threshold: float | None,
    contains: str | None,
) -> tuple[list[str], list[tuple[Any, ...]]]:
    embedding_json = json.dumps(embedding)

    sql = """
DECLARE @embedding_text nvarchar(max) = CAST(? AS nvarchar(max));
DECLARE @e vector(1536) = CAST(@embedding_text AS vector(1536));
DECLARE @max_rating int = ?;
DECLARE @distance_threshold float = ?;
DECLARE @contains nvarchar(200) = ?;

SELECT TOP (?)
    sf.feedback_text,
    sf.rating_overall_experience,
    vector_distance('cosine', @e, sf.feedback_vector) AS distance
FROM Service_Feedback sf
WHERE
    sf.feedback_vector IS NOT NULL
    AND (@max_rating IS NULL OR sf.rating_overall_experience <= @max_rating)
    AND (@distance_threshold IS NULL OR vector_distance('cosine', @e, sf.feedback_vector) <= @distance_threshold)
    AND (@contains IS NULL OR sf.feedback_text LIKE '%' + @contains + '%')
ORDER BY distance;
"""

    cursor = connection.cursor()
    cursor.execute(sql, embedding_json, max_rating, distance_threshold, contains, top)
    rows = cursor.fetchall()
    columns = [col[0] for col in cursor.description]
    cursor.close()
    return columns, rows


def print_table(columns: list[str], rows: list[tuple[Any, ...]]) -> None:
    if not rows:
        print("No rows found for the current filters.")
        return

    widths = [len(col) for col in columns]
    str_rows: list[list[str]] = []

    for row in rows:
        row_values = ["" if v is None else str(v) for v in row]
        str_rows.append(row_values)
        for i, value in enumerate(row_values):
            widths[i] = min(max(widths[i], len(value)), 100)

    def clip(value: str, width: int) -> str:
        if len(value) <= width:
            return value
        return value[: max(0, width - 3)] + "..."

    header = " | ".join(clip(columns[i], widths[i]).ljust(widths[i]) for i in range(len(columns)))
    separator = "-+-".join("-" * widths[i] for i in range(len(columns)))

    print(header)
    print(separator)
    for row in str_rows:
        print(" | ".join(clip(row[i], widths[i]).ljust(widths[i]) for i in range(len(columns))))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run vector search on Service_Feedback using Azure OpenAI embeddings + Azure SQL (Managed Identity)."
    )
    parser.add_argument("--query-text", required=True, help="Natural language text to embed for similarity search.")
    parser.add_argument("--top", type=int, default=10, help="Number of rows to return (default: 10).")
    parser.add_argument("--max-rating", type=int, default=None, help="Optional max rating filter (e.g., 3).")
    parser.add_argument(
        "--distance-threshold",
        type=float,
        default=None,
        help="Optional cosine-distance upper bound (e.g., 0.5).",
    )
    parser.add_argument(
        "--contains",
        default=None,
        help="Optional feedback text keyword filter (SQL LIKE contains).",
    )
    parser.add_argument(
        "--api-version",
        default=os.getenv("AZURE_OPENAI_EMBEDDINGS_API_VERSION", "2023-05-15"),
        help="Azure OpenAI embeddings API version (default: 2023-05-15 or env override).",
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()

    server = get_required_env("az_db_server")
    database = get_required_env("az_db_database")
    openai_endpoint = get_required_env("AZURE_OPENAI_ENDPOINT")
    embeddings_deployment = get_required_env("AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT_NAME")

    credential = DefaultAzureCredential()

    embedding = get_embedding(
        credential=credential,
        endpoint=openai_endpoint,
        deployment=embeddings_deployment,
        text=args.query_text,
        api_version=args.api_version,
    )

    with get_sql_connection(credential, server, database) as connection:
        columns, rows = run_vector_query(
            connection=connection,
            embedding=embedding,
            top=args.top,
            max_rating=args.max_rating,
            distance_threshold=args.distance_threshold,
            contains=args.contains,
        )

    print_table(columns, rows)


if __name__ == "__main__":
    main()
