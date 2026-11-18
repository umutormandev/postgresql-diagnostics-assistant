"""Read-only PostgreSQL diagnostics HTTP API."""

import os
from time import perf_counter
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from .auth import require_role
from .catalog import (
    FIXTURE_ROWS, needs_cost_clarification, requested_limits, resolve_cost_metric, select_schema,
)
from .services import (
    DatabasePermissionDenied, ServiceUnavailable, fetch_live_diagnostic,
    fetch_lock_waits, generate_method_sql,
)
from .sql_guard import UnsafeSQL, validate_candidate_sql


class DiagnoseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=1000)
    method: Literal["proposed", "b1", "b2", "b3", "a1"] = "proposed"
    metric: Literal["total", "mean"] | None = None


class DiagnoseResponse(BaseModel):
    status: str = "ok"
    intent: str = "lock_waits"
    rows: list[dict[str, int | float | str | None]]
    source: str = "pg_blocking_pids + pg_locks"
    clarification: dict[str, object] | None = None
    threshold_seconds: int | None = None
    window_minutes: int | None = None


app = FastAPI(title="PostgreSQL Diagnostics Assistant")


@app.post("/diagnose", response_model=DiagnoseResponse, response_model_exclude_none=True)
def diagnose(
    payload: DiagnoseRequest, request: Request, response: Response
) -> DiagnoseResponse | JSONResponse:
    role = require_role(request)
    try:
        schema = select_schema(payload.question, role)
        metric = resolve_cost_metric(payload.question, schema, payload.metric)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="forbidden") from exc
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

    fixtures = getattr(request.app.state, "controlled_fixture", None)
    demo = os.getenv("DIAGNOSTICS_DEMO_MODE") == "1"
    use_controlled = fixtures is not None or (demo and schema.intent != "lock_waits")
    database_variable = (
        "DIAGNOSTICS_VIEWER_DATABASE_URL" if role == "viewer"
        else "DIAGNOSTICS_DATABASE_URL"
    )
    if schema.intent != "lock_waits" and not use_controlled and (
        not schema.live or not os.getenv(database_variable)
    ):
        return JSONResponse(status_code=503, content={
            "status": "unavailable", "intent": schema.intent, "rows": [],
            "source": "unavailable",
            "detail": "Live diagnostic view is not available",
        })

    threshold, window, count = requested_limits(payload.question, schema)
    if window not in (None, 15):
        return DiagnoseResponse(
            status="insufficient_data", intent=schema.intent, rows=[],
            source="controlled_fixture" if use_controlled else schema.source,
            window_minutes=window,
        )

    try:
        if payload.method == "b1":
            candidate = schema.safe_sql
        else:
            options = {"role": role} if role != "operator" else {}
            candidate = generate_method_sql(payload.question, payload.method, schema, metric, **options)
        sql = validate_candidate_sql(candidate, schema)
    except UnsafeSQL as exc:
        raise HTTPException(status_code=502, detail="Model SQL is not allowed") from exc
    except ServiceUnavailable as exc:
        raise HTTPException(
            status_code=503, detail=str(exc), headers={"X-Diagnostics-Stage": "model"}
        ) from exc

    source = schema.source
    if schema.live and not use_controlled:
        started = perf_counter()
        try:
            rows = (
                fetch_lock_waits(sql) if schema.intent == "lock_waits"
                else fetch_live_diagnostic(sql, schema.intent, role)
            )
        except DatabasePermissionDenied as exc:
            raise HTTPException(
                status_code=403, detail="forbidden",
                headers={"X-Diagnostics-Stage": "database_denied"},
            ) from exc
        except ServiceUnavailable as exc:
            raise HTTPException(
                status_code=503, detail=str(exc), headers={"X-Diagnostics-Stage": "database"}
            ) from exc
        response.headers["Server-Timing"] = f"db;dur={(perf_counter() - started) * 1000:.3f}"
        if rows is None:
            return DiagnoseResponse(
                status="insufficient_data", intent=schema.intent, rows=[],
                source=source, window_minutes=15,
            )
    else:
        rows = [dict(row) for row in (fixtures if fixtures is not None else FIXTURE_ROWS)[schema.intent]]
        source = "controlled_fixture"
    if threshold is not None:
        rows = [row for row in rows if row["duration_seconds"] > threshold]
    if schema.intent == "query_stats" and metric:
        column = "total_exec_time_ms" if metric == "total" else "mean_exec_time_ms"
        rows = sorted(rows, key=lambda row: (-row[column], row["queryid"]))[:count]

    return DiagnoseResponse(
        intent=schema.intent, rows=rows, source=source, threshold_seconds=threshold,
        window_minutes=(window or 15) if schema.intent == "query_stats" else window,
    )
