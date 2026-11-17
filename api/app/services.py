"""External model and database adapters."""

import os

import httpx
import psycopg
from psycopg.rows import dict_row

from .catalog import CATALOG, Schema, visible_schemas


SYSTEM_PROMPT = (
    "Return only one PostgreSQL SELECT statement, with no markdown or explanation. "
    "The only available relation is diagnostics.lock_waits, with columns "
    "waiting_pid, blocking_pid, wait_seconds. Return all three columns. "
    "Do not use any other relations, expressions, functions, filters or clauses."
)


class ServiceUnavailable(RuntimeError):
    pass


def _chat_completion(messages: list[dict[str, str]]) -> str:
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
                    "messages": messages,
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


def generate_candidate_sql(question: str) -> str:
    """Keep the original lock prompt and endpoint behavior."""
    return _chat_completion([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ])


def generate_method_sql(
    question: str, method: str, selected: Schema, metric: str | None = None
) -> str:
    """All methods share the same HF model, timeout, temperature and token limit."""
    context_schemas = (selected,) if method == "proposed" else visible_schemas("operator")
    context = "; ".join(schema.describe() for schema in context_schemas)
    if method in {"b2", "b3"}:
        system = (
            "Generate one PostgreSQL SELECT for the question using this fixed schema: "
            f"{context}. Return only SQL. Select every column of the relevant view. "
            "No joins, functions, filters, ordering or other clauses."
        )
    elif method in {"a1", "proposed"}:
        system = (
            "You are a diagnostics SQL generator. Allowed schema: "
            f"{context}. Return one SELECT of every column of the relevant view only; "
            "no joins, functions, filters, ordering or other clauses."
        )
    else:
        raise ValueError("Unknown method")

    messages = [{"role": "system", "content": system}]
    if method == "b3":
        for example_question, example_sql in (
            ("Hangi oturum hangisini engelliyor?", CATALOG["lock_waits"].safe_sql),
            ("Uzun süren sorgular hangileri?", CATALOG["long_queries"].safe_sql),
            ("Sorgu istatistiklerini göster", CATALOG["query_stats"].safe_sql),
        ):
            messages.extend([
                {"role": "user", "content": example_question},
                {"role": "assistant", "content": example_sql},
            ])
    user_content = question if metric is None else f"{question}\nCost metric: {metric}"
    messages.append({"role": "user", "content": user_content})
    return _chat_completion(messages)


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
