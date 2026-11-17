"""External model and database adapters."""

import os

import httpx
import psycopg
from psycopg.rows import dict_row

from .catalog import CATALOG, Schema, visible_schemas


DEVELOPMENT_EXAMPLES = (
    ("Hangi oturum hangisini engelliyor?", CATALOG["lock_waits"].safe_sql),
    ("Uzun süren sorgular hangileri?", CATALOG["long_queries"].safe_sql),
    ("Sorgu istatistiklerini göster", CATALOG["query_stats"].safe_sql),
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
    """Backward-compatible lock helper using the proposed prompt builder."""
    return generate_method_sql(question, "proposed", CATALOG["lock_waits"])


def build_messages(
    question: str, method: str, selected: Schema, metric: str | None = None
) -> list[dict[str, str]]:
    """A1 and proposed differ only in their schema context, for every intent."""
    if method not in {"proposed", "a1", "b2", "b3"}:
        raise ValueError("Unknown method")
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
    messages = [{"role": "system", "content": system}]
    if method in {"b3", "a1", "proposed"}:
        for example_question, example_sql in DEVELOPMENT_EXAMPLES:
            messages.extend([
                {"role": "user", "content": example_question},
                {"role": "assistant", "content": example_sql},
            ])
    user_content = question if metric is None else f"{question}\nCost metric: {metric}"
    messages.append({"role": "user", "content": user_content})
    return messages


def generate_method_sql(
    question: str, method: str, selected: Schema, metric: str | None = None
) -> str:
    """All methods share the same HF model, timeout, temperature and token limit."""
    return _chat_completion(build_messages(question, method, selected, metric))


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
