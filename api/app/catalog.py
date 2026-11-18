"""Versioned, explicit diagnostic schemas and role permissions."""

from dataclasses import dataclass
import re
import unicodedata


@dataclass(frozen=True)
class Schema:
    intent: str
    view: str
    columns: tuple[str, ...]
    roles: tuple[str, ...]
    source: str
    live: bool

    @property
    def safe_sql(self) -> str:
        return f"SELECT {', '.join(self.columns)} FROM {self.view}"

    def describe(self) -> str:
        return f"{self.view}({', '.join(self.columns)})"


CATALOG = {
    "lock_waits": Schema(
        "lock_waits", "diagnostics.lock_waits",
        ("waiting_pid", "blocking_pid", "wait_seconds"),
        ("operator",), "pg_blocking_pids + pg_locks", True,
    ),
    "long_queries": Schema(
        "long_queries", "diagnostics.long_queries",
        ("pid", "duration_seconds", "query_text"),
        ("operator",), "pg_stat_activity", True,
    ),
    "query_stats": Schema(
        "query_stats", "diagnostics.query_stats",
        ("queryid", "total_exec_time_ms", "mean_exec_time_ms", "calls"),
        ("operator", "viewer"), "pg_stat_statements snapshots", True,
    ),
}

FIXTURE_ROWS = {
    "long_queries": [
        {"pid": 7312, "duration_seconds": 42, "query_text": "SELECT * FROM orders"},
    ],
    "query_stats": [
        {"queryid": 901, "total_exec_time_ms": 1200, "mean_exec_time_ms": 12, "calls": 100},
        {"queryid": 902, "total_exec_time_ms": 900, "mean_exec_time_ms": 90, "calls": 10},
    ],
}


def _normalize(question: str) -> str:
    normalized = unicodedata.normalize("NFKD", question.casefold().replace("ı", "i"))
    return "".join(char for char in normalized if not unicodedata.combining(char))


def select_schema(question: str, role: str) -> Schema:
    """Choose only an authorized schema from Turkish or English intent terms."""
    words = _normalize(question)
    if role not in ("operator", "viewer") or re.search(
        r"\b(sil|guncelle|kapat|sonlandir|degistir|iptal et|delete|update|terminate)\b", words
    ):
        raise PermissionError("forbidden")
    if any(term in words for term in (
        "pahali", "istatistik", "statistics", "toplam", "total",
        "ortalama", "cagri basina", "mean", "average",
    )):
        intent = "query_stats"
    elif any(term in words for term in ("uzun", "yavas", "aktif sorgu", "long", "slow", "active quer")):
        intent = "long_queries"
    elif any(term in words for term in ("kilit", "engell", "oturum", "lock", "block")):
        intent = "lock_waits"
    else:
        raise ValueError("Unsupported diagnostic question")
    schema = CATALOG[intent]
    if role not in schema.roles:
        raise PermissionError("forbidden")
    return schema


def visible_schemas(role: str) -> tuple[Schema, ...]:
    return tuple(schema for schema in CATALOG.values() if role in schema.roles)


def resolve_cost_metric(question: str, schema: Schema, metric: str | None) -> str | None:
    """Use an explicit metric in the question, or a matching request field."""
    if schema.intent != "query_stats":
        return metric
    words = _normalize(question)
    mentions_total = "toplam" in words or "total" in words
    mentions_mean = any(term in words for term in (
        "ortalama", "cagri basina", "mean", "average", "per call",
    ))
    inferred = ("total" if mentions_total else "mean") if mentions_total != mentions_mean else None
    if metric and inferred and metric != inferred:
        raise ValueError("Cost metric conflicts with the question")
    return metric or inferred


def needs_cost_clarification(question: str, schema: Schema, metric: str | None) -> bool:
    words = _normalize(question)
    return schema.intent == "query_stats" and metric is None and (
        "pahali" in words or "most expensive" in words
    )


def requested_limits(question: str, schema: Schema) -> tuple[int | None, int | None, int]:
    """Extract the optional duration, time window and result count."""
    words = _normalize(question)
    if schema.intent == "long_queries":
        duration = re.search(r"\b(\d{1,9})\s*saniye(?:yi|den)?\b", words)
        return (int(duration[1]) if duration else None), None, 1
    if schema.intent == "query_stats":
        window = re.search(r"\bson\s+(\d{1,9})\s*dakika", words)
        count = re.search(r"\b(\d{1,9})\s+sorgu", words)
        return None, (int(window[1]) if window else None), (int(count[1]) if count else 1)
    return None, None, 1
