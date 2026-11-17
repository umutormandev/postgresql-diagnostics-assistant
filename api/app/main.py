"""Lock diagnostics HTTP API."""

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from .auth import require_operator
from .catalog import FIXTURE_ROWS, needs_cost_clarification, select_schema
from .services import (
    ServiceUnavailable, fetch_lock_waits, generate_candidate_sql, generate_method_sql,
)
from .sql_guard import UnsafeSQL, validate_candidate_sql


class DiagnoseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=1000)
    method: Literal["proposed", "b2", "b3", "a1"] = "proposed"
    metric: Literal["total", "mean"] | None = None


class DiagnoseResponse(BaseModel):
    status: str = "ok"
    intent: str = "lock_waits"
    rows: list[dict[str, int | str]]
    source: str = "pg_blocking_pids + pg_locks"
    clarification: dict[str, object] | None = None


app = FastAPI(title="PostgreSQL Diagnostics Assistant")


@app.post("/diagnose", response_model=DiagnoseResponse, response_model_exclude_none=True)
def diagnose(payload: DiagnoseRequest, request: Request) -> DiagnoseResponse:
    role = require_operator(request)
    try:
        schema = select_schema(payload.question, role)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if needs_cost_clarification(payload.question, schema, payload.metric):
        return DiagnoseResponse(
            status="clarification_required", intent="query_stats", rows=[],
            source="schema_catalog",
            clarification={
                "question": "Toplam çalışma süresi mi, çağrı başına ortalama süre mi?",
                "options": ["total", "mean"],
            },
        )

    try:
        if payload.method == "proposed" and schema.intent == "lock_waits":
            candidate = generate_candidate_sql(payload.question)
        else:
            candidate = generate_method_sql(
                payload.question, payload.method, schema, payload.metric
            )
        sql = validate_candidate_sql(candidate, schema)
        if schema.live:
            rows = fetch_lock_waits(sql)
        else:
            rows = [dict(row) for row in FIXTURE_ROWS[schema.intent]]
            if schema.intent == "query_stats" and payload.metric:
                column = (
                    "total_exec_time_ms" if payload.metric == "total"
                    else "mean_exec_time_ms"
                )
                rows = [max(rows, key=lambda row: row[column])]
    except UnsafeSQL as exc:
        raise HTTPException(status_code=502, detail="Model SQL is not allowed") from exc
    except ServiceUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return DiagnoseResponse(intent=schema.intent, rows=rows, source=schema.source)
