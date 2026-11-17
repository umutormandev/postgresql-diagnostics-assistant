"""Live Ollama + isolated PostgreSQL pilot through POST /diagnose."""

import json
import os
from pathlib import Path
import secrets
import threading
import time
from uuid import uuid4

import jwt
import psycopg
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo

from app import main
from app.sql_guard import UnsafeSQL, validate_candidate_sql


QUESTION = "Hangi oturumlar kilit bekliyor?"
METHODS = ("b2", "b3", "a1", "proposed")
API_ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = API_ROOT / ".pilot"


def db_url(admin_url: str, name: str, **overrides: str) -> str:
    return make_conninfo(admin_url, dbname=name, **overrides)


def wait_for_block(admin_url: str, name: str, waiter_pid: int, blocker_pid: int) -> None:
    with psycopg.connect(db_url(admin_url, name), autocommit=True) as observer:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            row = observer.execute(
                "SELECT pg_blocking_pids(pid) FROM pg_stat_activity WHERE pid = %s",
                (waiter_pid,),
            ).fetchone()
            if row and blocker_pid in row[0]:
                return
            time.sleep(0.05)
    raise AssertionError("The real PostgreSQL lock wait did not start")


def bearer(secret: str) -> dict[str, str]:
    now = int(time.time())
    token = jwt.encode({
        "sub": "ollama-pilot", "role": "operator",
        "iss": "postgresql-diagnostics-auth", "aud": "postgresql-diagnostics-api",
        "iat": now, "exp": now + 3600,
    }, secret, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


def main_pilot() -> None:
    admin_url = os.environ["TEST_POSTGRES_ADMIN_URL"]
    os.environ["DIAGNOSTICS_MODEL_PROVIDER"] = "ollama"
    os.environ["OLLAMA_BASE_URL"] = "http://127.0.0.1:11434"
    os.environ["OLLAMA_MODEL"] = "qwen2.5-coder:3b"
    os.environ.pop("DIAGNOSTICS_DEMO_MODE", None)
    secret = secrets.token_urlsafe(48)
    os.environ["DIAGNOSTICS_AUTH_SECRET"] = secret
    suffix = uuid4().hex[:10]
    name = f"lock_ollama_pilot_{suffix}"
    reader_role = f"lock_ollama_reader_{suffix}"
    reader_password = secrets.token_urlsafe(32)
    candidate_records: list[dict] = []
    api_records: list[dict] = []
    phase = "waiting"
    original_generate = main.generate_method_sql

    def recording_generate(question, method, schema, metric=None):
        candidate = original_generate(question, method, schema, metric)
        record = {
            "candidate_id": f"{phase}-{method}", "phase": phase,
            "method": method, "role": "operator", "question": question,
            "sql": candidate,
        }
        try:
            record["normalized_sql"] = validate_candidate_sql(candidate, schema)
            record["accepted"] = True
        except UnsafeSQL as exc:
            record["accepted"] = False
            record["reason"] = str(exc)
        candidate_records.append(record)
        return candidate

    main.generate_method_sql = recording_generate
    try:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            try:
                admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
                admin.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(reader_role), sql.Literal(reader_password)
                ))
                with psycopg.connect(db_url(admin_url, name), autocommit=True) as setup:
                    setup.execute("CREATE TABLE lock_target (id integer)")
                    setup.execute((API_ROOT / "sql" / "001_lock_waits.sql").read_text())
                    setup.execute(sql.SQL("GRANT USAGE ON SCHEMA diagnostics TO {}").format(
                        sql.Identifier(reader_role)
                    ))
                    setup.execute(sql.SQL("GRANT SELECT ON diagnostics.lock_waits TO {}").format(
                        sql.Identifier(reader_role)
                    ))
                os.environ["DIAGNOSTICS_DATABASE_URL"] = db_url(
                    admin_url, name, user=reader_role, password=reader_password
                )
                headers = bearer(secret)
                with TestClient(main.app) as client:
                    with (
                        psycopg.connect(db_url(admin_url, name), autocommit=True) as blocker,
                        psycopg.connect(db_url(admin_url, name), autocommit=True) as waiter,
                    ):
                        blocker_pid = blocker.execute("SELECT pg_backend_pid()").fetchone()[0]
                        waiter_pid = waiter.execute("SELECT pg_backend_pid()").fetchone()[0]
                        worker_errors = []

                        def blocked_select():
                            try:
                                waiter.execute("SELECT * FROM lock_target")
                            except Exception as exc:
                                worker_errors.append(str(exc))

                        with blocker.transaction():
                            blocker.execute("LOCK TABLE lock_target IN ACCESS EXCLUSIVE MODE")
                            worker = threading.Thread(target=blocked_select, daemon=True)
                            worker.start()
                            wait_for_block(admin_url, name, waiter_pid, blocker_pid)
                            for method in METHODS:
                                response = client.post(
                                    "/diagnose", json={"question": QUESTION, "method": method},
                                    headers=headers,
                                )
                                body = response.json()
                                api_records.append({
                                    "phase": phase, "method": method,
                                    "http_status": response.status_code, "response": body,
                                    "expected_waiting_pid": waiter_pid,
                                    "expected_blocking_pid": blocker_pid,
                                })
                                assert response.status_code == 200, (method, body)
                                assert len(body["rows"]) == 1, (method, body)
                                assert body["rows"][0]["waiting_pid"] == waiter_pid
                                assert body["rows"][0]["blocking_pid"] == blocker_pid
                        worker.join(timeout=10)
                        assert not worker.is_alive() and not worker_errors

                    phase = "released"
                    for method in METHODS:
                        response = client.post(
                            "/diagnose", json={"question": QUESTION, "method": method},
                            headers=headers,
                        )
                        body = response.json()
                        api_records.append({
                            "phase": phase, "method": method,
                            "http_status": response.status_code, "response": body,
                        })
                        assert response.status_code == 200, (method, body)
                        assert body["rows"] == [], (method, body)
            finally:
                admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(name)
                ))
                admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(
                    sql.Identifier(reader_role)
                ))
    finally:
        main.generate_method_sql = original_generate
        ARTIFACTS.mkdir(exist_ok=True)
        (ARTIFACTS / "ollama_candidates.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in candidate_records),
            encoding="utf-8",
        )
        (ARTIFACTS / "ollama_api_results.json").write_text(
            json.dumps(api_records, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    print(json.dumps({
        "model": os.environ["OLLAMA_MODEL"], "base_url": os.environ["OLLAMA_BASE_URL"],
        "candidate_count": len(candidate_records), "api_call_count": len(api_records),
        "waiting_pid": waiter_pid, "blocking_pid": blocker_pid,
        "all_candidates_accepted": all(row["accepted"] for row in candidate_records),
        "artifacts": str(ARTIFACTS),
    }, ensure_ascii=True))


if __name__ == "__main__":
    main_pilot()
