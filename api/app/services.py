"""External model and database adapters."""

import os

import httpx
import psycopg
from psycopg.rows import dict_row


SYSTEM_PROMPT = (
    "Return only one PostgreSQL SELECT statement, with no markdown or explanation. "
    "The only available relation is diagnostics.lock_waits, with columns "
    "waiting_pid, blocking_pid, wait_seconds. Return all three columns. "
    "Do not use any other relations, expressions, functions, filters or clauses."
)


class ServiceUnavailable(RuntimeError):
    pass


def generate_candidate_sql(question: str) -> str:
    token = os.getenv("HF_TOKEN")
    model = os.getenv("HF_MODEL")
    if not token or not model:
        raise ServiceUnavailable("Hugging Face is not configured")

    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.post(
                "https://router.huggingface.co/v1/chat/completions",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": question},
                    ],
                    "max_tokens": 128,
                    "temperature": 0,
                },
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError("Missing model content")
            return content.strip()
    except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
        raise ServiceUnavailable("Hugging Face request failed") from exc


def fetch_lock_waits(sql: str) -> list[dict]:
    database_url = os.getenv("DIAGNOSTICS_DATABASE_URL")
    if not database_url:
        raise ServiceUnavailable("PostgreSQL is not configured")

    try:
        with psycopg.connect(
            database_url, autocommit=True, connect_timeout=5, row_factory=dict_row
        ) as connection:
            connection.read_only = True
            with connection.transaction():
                connection.execute(
                    "SELECT set_config('statement_timeout', %s, true)", ("3000ms",)
                )
                return connection.execute(sql).fetchall()
    except psycopg.Error as exc:
        raise ServiceUnavailable("PostgreSQL query failed") from exc
