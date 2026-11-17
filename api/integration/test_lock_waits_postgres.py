"""Run explicitly against a disposable PostgreSQL cluster with two databases."""

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
from app.sql_guard import SAFE_SQL


VIEW_SQL = (Path(__file__).resolve().parents[1] / "sql" / "001_lock_waits.sql").read_text()


def _database_url(admin_url: str, dbname: str, **overrides: str) -> str:
    return make_conninfo(admin_url, dbname=dbname, **overrides)


def _wait_for_block(admin_url: str, dbname: str, waiting_pid: int, blocking_pid: int) -> None:
    with psycopg.connect(_database_url(admin_url, dbname), autocommit=True) as observer:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            observed = observer.execute(
                "SELECT datname, pg_blocking_pids(pid) FROM pg_stat_activity WHERE pid = %s",
                (waiting_pid,),
            ).fetchone()
            if observed and observed[0] == dbname and blocking_pid in observed[1]:
                return
            time.sleep(0.05)
    raise AssertionError(f"PID {waiting_pid} did not block in database {dbname}")


def _operator_token() -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": "integration-operator",
            "role": "operator",
            "iss": "postgresql-diagnostics-auth",
            "aud": "postgresql-diagnostics-api",
            "iat": now,
            "exp": now + 300,
        },
        os.environ["DIAGNOSTICS_AUTH_SECRET"],
        algorithm="HS256",
    )


def _assert_lock_scenario(
    admin_url: str, dbname: str, client: TestClient, expected_visible: bool
) -> None:
    database_url = _database_url(admin_url, dbname)
    with (
        psycopg.connect(database_url, autocommit=True) as blocker,
        psycopg.connect(database_url, autocommit=True) as waiter,
    ):
        blocking_pid = blocker.execute("SELECT pg_backend_pid()").fetchone()[0]
        waiting_pid = waiter.execute("SELECT pg_backend_pid()").fetchone()[0]
        waiter_errors = []

        def blocked_select() -> None:
            try:
                waiter.execute("SELECT * FROM lock_target")
            except Exception as exc:
                waiter_errors.append(exc)

        with blocker.transaction():
            blocker.execute("LOCK TABLE lock_target IN ACCESS EXCLUSIVE MODE")
            worker = threading.Thread(target=blocked_select, daemon=True)
            worker.start()
            _wait_for_block(admin_url, dbname, waiting_pid, blocking_pid)

            response = client.post(
                "/diagnose",
                json={"question": "Which session blocks which?"},
                headers={"Authorization": f"Bearer {_operator_token()}"},
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["status"] == "ok"
            assert body["intent"] == "lock_waits"
            assert body["source"] == "pg_blocking_pids + pg_locks"
            if expected_visible:
                assert len(body["rows"]) == 1
                assert body["rows"][0]["waiting_pid"] == waiting_pid
                assert body["rows"][0]["blocking_pid"] == blocking_pid
                assert body["rows"][0]["wait_seconds"] >= 0
            else:
                assert body["rows"] == []

        worker.join(timeout=5)
        assert not worker.is_alive()
        assert not waiter_errors


def test_api_scopes_lock_waits_to_connected_database(monkeypatch) -> None:
    admin_url = os.environ["TEST_POSTGRES_ADMIN_URL"]
    suffix = uuid4().hex[:10]
    target_db = f"lock_diag_target_{suffix}"
    other_db = f"lock_diag_other_{suffix}"
    reader_role = f"lock_diag_reader_{suffix}"
    reader_password = secrets.token_urlsafe(32)

    with psycopg.connect(admin_url, autocommit=True) as admin:
        try:
            for dbname in (target_db, other_db):
                admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
            admin.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(reader_role), sql.Literal(reader_password)
                )
            )
            for dbname in (target_db, other_db):
                with psycopg.connect(_database_url(admin_url, dbname), autocommit=True) as setup:
                    setup.execute("CREATE TABLE lock_target (id integer)")
                    if dbname == target_db:
                        setup.execute(VIEW_SQL)
                        setup.execute(
                            sql.SQL("GRANT USAGE ON SCHEMA diagnostics TO {}").format(
                                sql.Identifier(reader_role)
                            )
                        )
                        setup.execute(
                            sql.SQL("GRANT SELECT ON diagnostics.lock_waits TO {}").format(
                                sql.Identifier(reader_role)
                            )
                        )

            monkeypatch.setenv(
                "DIAGNOSTICS_DATABASE_URL",
                _database_url(
                    admin_url, target_db, user=reader_role, password=reader_password
                ),
            )
            monkeypatch.setenv(
                "DIAGNOSTICS_AUTH_SECRET", "integration-secret-with-at-least-32-characters"
            )
            monkeypatch.setattr(main, "generate_candidate_sql", lambda question: SAFE_SQL)
            client = TestClient(main.app)

            _assert_lock_scenario(admin_url, target_db, client, expected_visible=True)
            _assert_lock_scenario(admin_url, other_db, client, expected_visible=False)
        finally:
            for dbname in (target_db, other_db):
                admin.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                        sql.Identifier(dbname)
                    )
                )
            admin.execute(
                sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(reader_role))
            )
