"""Lock diagnostics HTTP API."""

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .auth import require_operator
from .services import ServiceUnavailable, fetch_lock_waits, generate_candidate_sql
from .sql_guard import UnsafeSQL, validate_candidate_sql


class DiagnoseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=1000)


class LockWait(BaseModel):
    waiting_pid: int
    blocking_pid: int
    wait_seconds: int


class DiagnoseResponse(BaseModel):
    status: str = "ok"
    intent: str = "lock_waits"
    rows: list[LockWait]
    source: str = "pg_blocking_pids + pg_locks"


app = FastAPI(title="PostgreSQL Diagnostics Assistant")


@app.post("/diagnose", response_model=DiagnoseResponse)
def diagnose(payload: DiagnoseRequest, request: Request) -> DiagnoseResponse:
    require_operator(request)

    try:
        candidate = generate_candidate_sql(payload.question)
        sql = validate_candidate_sql(candidate)
        rows = fetch_lock_waits(sql)
    except UnsafeSQL as exc:
        raise HTTPException(status_code=502, detail="Model SQL is not allowed") from exc
    except ServiceUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return DiagnoseResponse(rows=[LockWait.model_validate(row) for row in rows])
