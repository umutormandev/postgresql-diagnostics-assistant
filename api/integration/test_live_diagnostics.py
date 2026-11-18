"""Exercise the three API diagnostics against a disposable PostgreSQL 17 database."""

import os
from pathlib import Path
import secrets
import threading
import time
from uuid import uuid4

import jwt
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.main import app


SQL_FILES = sorted((Path(__file__).resolve().parents[1] / "sql").glob("[0-9]*.sql"))


def token(role: str) -> dict[str, str]:
    now = int(time.time())
    signed = jwt.encode({
        "sub": "live-test", "role": role, "iss": "postgresql-diagnostics-auth",
        "aud": "postgresql-diagnostics-api", "iat": now, "exp": now + 300,
    }, os.environ["DIAGNOSTICS_AUTH_SECRET"], algorithm="HS256")
    return {"Authorization": f"Bearer {signed}"}


def ask(client: TestClient, role: str, question: str, **options):
    return client.post("/diagnose", json={"question": question, "method": "b1", **options},
                       headers=token(role))


@pytest.fixture
def live_db(monkeypatch):
    admin_url = os.environ["TEST_POSTGRES_ADMIN_URL"]
    suffix = uuid4().hex[:10]
    dbname = f"live_diag_{suffix}"
    operator = f"diag_operator_{suffix}"
    viewer = f"diag_viewer_{suffix}"
    sampler = f"diag_sampler_{suffix}"
    passwords = {role: secrets.token_urlsafe(24)
                 for role in (operator, viewer, sampler)}
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
        try:
            db_url = make_conninfo(admin_url, dbname=dbname)
            with psycopg.connect(db_url, autocommit=True) as setup:
                for path in SQL_FILES:
                    setup.execute(path.read_text(encoding="utf-8"))
                setup.execute("CREATE TABLE lock_target (id integer)")
            for role, membership in ((operator, "diagnostics_operator_reader"),
                                     (viewer, "diagnostics_viewer_reader"),
                                     (sampler, "diagnostics_sampler")):
                admin.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(passwords[role])))
                admin.execute(sql.SQL("GRANT {} TO {}").format(
                    sql.Identifier(membership), sql.Identifier(role)))
                admin.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(dbname), sql.Identifier(role)))
            monkeypatch.setenv("DIAGNOSTICS_AUTH_SECRET", "live-integration-secret-with-32-characters")
            monkeypatch.setenv("DIAGNOSTICS_DATABASE_URL", make_conninfo(
                db_url, user=operator, password=passwords[operator]))
            monkeypatch.setenv("DIAGNOSTICS_VIEWER_DATABASE_URL", make_conninfo(
                db_url, user=viewer, password=passwords[viewer]))
            monkeypatch.delenv("DIAGNOSTICS_DEMO_MODE", raising=False)
            app.state.controlled_fixture = None
            yield db_url, {name: make_conninfo(db_url, user=name, password=passwords[name])
                           for name in (operator, viewer, sampler)}, operator, viewer, sampler
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(dbname)))
            for role in (operator, viewer, sampler):
                admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


def test_live_views_api_and_permissions(live_db):
    db_url, role_urls, operator, viewer, sampler = live_db
    client = TestClient(app)
    assert (conninfo_to_dict(role_urls[operator])["password"]
            != conninfo_to_dict(role_urls[viewer])["password"])
    with psycopg.connect(db_url, autocommit=True) as admin:
        assert admin.execute("SELECT extname FROM pg_extension WHERE extname='pg_stat_statements'").fetchone()
        assert admin.execute("SELECT diagnostics.capture_query_stats()").fetchone()[0] >= 1
        assert ask(client, "operator", "Hangi oturumlar kilit bekliyor?").json()["rows"] == []
        assert ask(client, "operator", "Uzun sorgular hangileri?").status_code == 200
        early = ask(client, "viewer", "Son 15 dakikada toplam süreye göre ilk 2 sorgu hangisi?")
        assert early.status_code == 200
        assert early.json()["status"] == "insufficient_data"

        with psycopg.connect(role_urls[viewer], autocommit=True) as restricted:
            for table in ("diagnostics.lock_waits", "diagnostics.long_queries",
                          "diagnostics.query_capture_batches", "public.pg_stat_statements"):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    restricted.execute(f"SELECT * FROM {table}").fetchall()
            assert restricted.execute("SELECT * FROM diagnostics.query_stats").fetchall() == []
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                restricted.execute("SELECT diagnostics.capture_query_stats()")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                restricted.execute("SELECT * FROM diagnostics.current_long_queries()")
        with psycopg.connect(role_urls[operator], autocommit=True) as restricted:
            assert not restricted.execute("SELECT pg_has_role(current_user, 'pg_read_all_stats', 'member')").fetchone()[0]
            own_pid = restricted.execute("SELECT pg_backend_pid()").fetchone()[0]
            restricted.execute("SELECT * FROM diagnostics.lock_waits").fetchall()
            assert all(row[0] != own_pid for row in restricted.execute(
                "SELECT pid FROM diagnostics.long_queries").fetchall())
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                restricted.execute("INSERT INTO diagnostics.query_capture_batches (dbid, stats_reset, dealloc) VALUES (1, now(), 0)")
        with psycopg.connect(role_urls[sampler], autocommit=True) as sampler_conn:
            assert sampler_conn.execute("SELECT diagnostics.capture_query_stats()").fetchone()[0] >= 1

        # Build a 15-minute window with known counter deltas; no model output is involved.
        dbid = admin.execute("SELECT oid FROM pg_database WHERE datname=current_database()").fetchone()[0]
        reset, dealloc = admin.execute(
            "SELECT stats_reset, dealloc FROM public.pg_stat_statements_info").fetchone()
        batch_ids = []
        for minutes in (15.5, 0):
            batch_ids.append(admin.execute("""
                INSERT INTO diagnostics.query_capture_batches
                    (dbid, captured_at, stats_reset, dealloc)
                VALUES (%s, clock_timestamp() - (%s * interval '1 minute'), %s, %s)
                RETURNING batch_id
            """, (dbid, minutes, reset, dealloc)).fetchone()[0])
        for batch, values in ((batch_ids[0], ((101, 10, 100), (9007199254740993, 10, 100))),
                              (batch_ids[1], ((101, 110, 1300), (9007199254740993, 20, 1000)))):
            for queryid, calls, total in values:
                admin.execute("""
                    INSERT INTO diagnostics.query_counter_snapshots
                        (batch_id, userid, dbid, queryid, toplevel, calls, total_exec_time_ms)
                    VALUES (%s, 1, %s, %s, true, %s, %s)
                """, (batch, dbid, queryid, calls, total))
        assert admin.execute("SELECT sufficient FROM diagnostics.query_stats_health").fetchone()[0]
        total = ask(client, "viewer", "Son 15 dakikada toplam süreye göre ilk 2 sorgu hangisi?")
        mean = ask(client, "operator", "Son 15 dakikada çağrı başına ortalama süreye göre ilk 2 sorgu hangisi?")
        assert total.status_code == mean.status_code == 200
        assert [row["queryid"] for row in total.json()["rows"]] == ["101", "9007199254740993"]
        assert [row["queryid"] for row in mean.json()["rows"]] == ["9007199254740993", "101"]
        assert total.json()["rows"][0]["calls"] == 100
        assert total.json()["rows"][0]["mean_exec_time_ms"] == 12
        assert all("query_text" not in row for row in total.json()["rows"])
        for question in ("Hangi oturumlar kilit bekliyor?", "Uzun sorgular hangileri?"):
            assert ask(client, "viewer", question).status_code == 403
        admin.execute("UPDATE diagnostics.query_capture_batches SET dealloc=dealloc+1 WHERE batch_id=%s",
                      (batch_ids[1],))
        assert ask(client, "viewer", "Son 15 dakikada toplam süreye göre ilk 2 sorgu hangisi?").json()["status"] == "insufficient_data"
        admin.execute("UPDATE diagnostics.query_capture_batches SET dealloc=dealloc-1 WHERE batch_id=%s",
                      (batch_ids[1],))
        admin.execute("UPDATE diagnostics.query_capture_batches SET stats_reset=stats_reset + interval '1 second' WHERE batch_id=%s",
                      (batch_ids[1],))
        assert ask(client, "viewer", "Son 15 dakikada toplam süreye göre ilk 2 sorgu hangisi?").json()["status"] == "insufficient_data"
        admin.execute("UPDATE diagnostics.query_capture_batches SET stats_reset=stats_reset - interval '1 second' WHERE batch_id=%s",
                      (batch_ids[1],))
        admin.execute("UPDATE diagnostics.query_counter_snapshots SET calls=1 WHERE batch_id=%s AND queryid=101",
                      (batch_ids[1],))
        assert ask(client, "viewer", "Son 15 dakikada toplam süreye göre ilk 2 sorgu hangisi?").json()["status"] == "insufficient_data"


def test_real_lock_and_long_query_over_ten_seconds(live_db):
    db_url, _, _, _, _ = live_db
    client = TestClient(app)
    with (psycopg.connect(db_url, autocommit=True) as blocker,
          psycopg.connect(db_url, autocommit=True) as waiter,
          psycopg.connect(db_url, autocommit=True) as sleeper):
        blocking_pid = blocker.execute("SELECT pg_backend_pid()").fetchone()[0]
        waiting_pid = waiter.execute("SELECT pg_backend_pid()").fetchone()[0]
        sleeping_pid = sleeper.execute("SELECT pg_backend_pid()").fetchone()[0]
        problems = []

        def worker(connection, statement):
            try:
                connection.execute(statement)
            except Exception as exc:
                problems.append(exc)

        with blocker.transaction():
            blocker.execute("LOCK TABLE lock_target IN ACCESS EXCLUSIVE MODE")
            waiting = threading.Thread(target=worker, args=(waiter, "SELECT * FROM lock_target"), daemon=True)
            sleeping = threading.Thread(target=worker, args=(sleeper, "SELECT pg_sleep(18)"), daemon=True)
            waiting.start()
            sleeping.start()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                observed = blocker.execute("SELECT pg_blocking_pids(%s)", (waiting_pid,)).fetchone()[0]
                if blocking_pid in observed:
                    break
                time.sleep(.05)
            else:
                pytest.fail("Real lock wait did not start")
            locks = ask(client, "operator", "Hangi oturumlar kilit bekliyor?")
            assert locks.status_code == 200, locks.text
            assert any(row["waiting_pid"] == waiting_pid and row["blocking_pid"] == blocking_pid
                       for row in locks.json()["rows"])
            time.sleep(10.3)
            observed_long = blocker.execute("SELECT pid, state, query_start, query FROM pg_stat_activity WHERE pid=%s", (sleeping_pid,)).fetchone()
            direct_long = blocker.execute("SELECT pid, duration_seconds FROM diagnostics.long_queries WHERE pid=%s", (sleeping_pid,)).fetchone()
            longs = ask(client, "operator", "10 saniyeyi aşan uzun sorguları listele")
            assert longs.status_code == 200, longs.text
            assert any(row["pid"] == sleeping_pid and row["duration_seconds"] > 10
                       for row in longs.json()["rows"]), (observed_long, direct_long, longs.text)
            assert all(row["pid"] != blocker.execute("SELECT pg_backend_pid()").fetchone()[0]
                       for row in longs.json()["rows"])
        waiting.join(timeout=5)
        sleeping.join(timeout=5)
        assert not problems
        assert ask(client, "operator", "Hangi oturumlar kilit bekliyor?").json()["rows"] == []


def test_long_query_in_other_database_is_invisible(live_db):
    other_url = os.environ["TEST_POSTGRES_ADMIN_URL"]
    client = TestClient(app)
    with psycopg.connect(other_url, autocommit=True) as sleeper:
        thread = threading.Thread(target=lambda: sleeper.execute("SELECT pg_sleep(13)"), daemon=True)
        thread.start()
        time.sleep(10.3)
        response = ask(client, "operator", "10 saniyeyi aşan uzun sorguları listele")
        assert response.status_code == 200, response.text
        assert response.json()["rows"] == []
        thread.join(timeout=5)
