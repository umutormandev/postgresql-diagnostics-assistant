"""Lock diagnostics HTTP API."""

import os
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from .auth import require_operator
from .catalog import (
    FIXTURE_ROWS, needs_cost_clarification, resolve_cost_metric, select_schema,
)
from .services import (
    ServiceUnavailable, fetch_lock_waits, generate_method_sql,
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
def diagnose(payload: DiagnoseRequest, request: Request) -> DiagnoseResponse | JSONResponse:
    role = require_operator(request)
    try:
        schema = select_schema(payload.question, role)
        metric = resolve_cost_metric(payload.question, schema, payload.metric)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if needs_cost_clarification(payload.question, schema, metric):
        return DiagnoseResponse(
            status="clarification_required", intent="query_stats", rows=[],
            source="schema_catalog",
            clarification={
                "question": "Toplam çalışma süresi mi, çağrı başına ortalama süre mi?",
                "options": ["total", "mean"],
            },
        )

    if not schema.live and os.getenv("DIAGNOSTICS_DEMO_MODE") != "1":
        return JSONResponse(status_code=503, content={
            "status": "unavailable", "intent": schema.intent, "rows": [],
            "source": "unavailable",
            "detail": "Live diagnostic view is not available",
        })

    try:
        candidate = generate_method_sql(payload.question, payload.method, schema, metric)
        sql = validate_candidate_sql(candidate, schema)
        if schema.live:
            rows = fetch_lock_waits(sql)
        else:
            rows = [dict(row) for row in FIXTURE_ROWS[schema.intent]]
            if schema.intent == "query_stats" and metric:
                column = (
                    "total_exec_time_ms" if metric == "total"
                    else "mean_exec_time_ms"
                )
                rows = [max(rows, key=lambda row: row[column])]
    except UnsafeSQL as exc:
        raise HTTPException(status_code=502, detail="Model SQL is not allowed") from exc
    except ServiceUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return DiagnoseResponse(intent=schema.intent, rows=rows, source=schema.source)
