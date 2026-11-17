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


class DatabasePermissionDenied(RuntimeError):
    pass


def _chat_completion(messages: list[dict[str, str]]) -> str:
    provider = os.getenv("DIAGNOSTICS_MODEL_PROVIDER", "huggingface").lower()
    if provider == "ollama":
        return _ollama_completion(messages)
    if provider != "huggingface":
        raise ServiceUnavailable("Unknown model provider")
    return _huggingface_completion(messages)


def _huggingface_completion(messages: list[dict[str, str]]) -> str:
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


def _ollama_completion(messages: list[dict[str, str]]) -> str:
    base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    model = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:3b")
    try:
        timeout = httpx.Timeout(240.0, connect=10.0)
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            response = client.post(
                f"{base_url}/api/chat",
                json={
                    "model": model,
                    "messages": messages,
                    "stream": False,
                    "options": {"temperature": 0, "num_predict": 128},
                },
            )
            response.raise_for_status()
            content = response.json()["message"]["content"]
            if not isinstance(content, str):
                raise ValueError("Missing model content")
            return content.strip()
    except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
        raise ServiceUnavailable("Ollama request failed") from exc


def generate_candidate_sql(question: str) -> str:
    """Backward-compatible lock helper using the proposed prompt builder."""
    return generate_method_sql(question, "proposed", CATALOG["lock_waits"])


def build_messages(
    question: str, method: str, selected: Schema, metric: str | None = None,
    *, role: str = "operator",
) -> list[dict[str, str]]:
    """A1 and proposed differ only in their schema context, for every intent."""
    if method not in {"proposed", "a1", "b2", "b3"}:
        raise ValueError("Unknown method")
    allowed = visible_schemas(role)
    if selected not in allowed:
        raise PermissionError("forbidden")
    context_schemas = (selected,) if method == "proposed" else allowed
    context = "; ".join(schema.describe() for schema in context_schemas)
    if method in {"b2", "b3"}:
        system = (
            "Generate one PostgreSQL SELECT for the question using this fixed schema: "
            f"{context}. Return only SQL. Select every column of the relevant view. "
            "Spell out each column name; never use SELECT *. Do not add markdown "
            "fences, explanations or a semicolon. No joins, functions, filters, "
            "ordering or other clauses."
        )
    elif method in {"a1", "proposed"}:
        system = (
            "You are a diagnostics SQL generator. Allowed schema: "
            f"{context}. Return one SELECT of every column of the relevant view only; "
            "spell out each column name; never use SELECT *. Do not add markdown "
            "fences, explanations or a semicolon. No joins, functions, filters, "
            "ordering or other clauses."
        )
    messages = [{"role": "system", "content": system}]
    if method in {"b3", "a1", "proposed"}:
        allowed_sql = {schema.safe_sql for schema in allowed}
        for example_question, example_sql in DEVELOPMENT_EXAMPLES:
            if example_sql not in allowed_sql:
                continue
            messages.extend([
                {"role": "user", "content": example_question},
                {"role": "assistant", "content": example_sql},
            ])
    user_content = question if metric is None else f"{question}\nCost metric: {metric}"
    messages.append({"role": "user", "content": user_content})
    return messages


def generate_method_sql(
    question: str, method: str, selected: Schema, metric: str | None = None,
    *, role: str = "operator",
) -> str:
    """All methods share the selected provider, model and generation options."""
    return _chat_completion(build_messages(question, method, selected, metric, role=role))


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
    except psycopg.errors.InsufficientPrivilege as exc:
        raise DatabasePermissionDenied("PostgreSQL role denied the query") from exc
    except psycopg.Error as exc:
        raise ServiceUnavailable("PostgreSQL query failed") from exc
