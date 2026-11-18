import time

import jwt
import pytest
from fastapi.testclient import TestClient

from app import main
from app.sql_guard import SAFE_SQL, UnsafeSQL, validate_candidate_sql
from app.catalog import CATALOG


SECRET = "test-secret-with-at-least-thirty-two-characters"
client = TestClient(main.app)


@pytest.fixture(autouse=True)
def auth_secret(monkeypatch):
    monkeypatch.setenv("DIAGNOSTICS_AUTH_SECRET", SECRET)


def bearer(role, secret=SECRET):
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "test-user",
            "role": role,
            "iss": "postgresql-diagnostics-auth",
            "aud": "postgresql-diagnostics-api",
            "iat": now,
            "exp": now + 300,
        },
        secret,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def test_operator_gets_lock_waits(monkeypatch):
    monkeypatch.setattr(main, "generate_method_sql", lambda *args: SAFE_SQL)
    observed = []

    def fake_fetch(sql):
        observed.append(sql)
        return [{"waiting_pid": 7426, "blocking_pid": 7412, "wait_seconds": 18}]

    monkeypatch.setattr(main, "fetch_lock_waits", fake_fetch)
    response = client.post(
        "/diagnose",
        json={"question": "Hangi oturum hangisini engelliyor?"},
        headers=bearer("operator"),
    )
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "intent": "lock_waits",
        "rows": [{"waiting_pid": 7426, "blocking_pid": 7412, "wait_seconds": 18}],
        "source": "pg_blocking_pids + pg_locks",
    }
    assert observed == [SAFE_SQL]


def test_viewer_is_forbidden_before_model_or_database(monkeypatch):
    def fail(*args):
        pytest.fail("Viewer must not call model or database")

    monkeypatch.setattr(main, "generate_method_sql", fail)
    monkeypatch.setattr(main, "fetch_lock_waits", fail)
    response = client.post(
        "/diagnose", json={"question": "locks"}, headers=bearer("viewer")
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "forbidden"}


def test_empty_lock_waits_return_empty_rows(monkeypatch):
    monkeypatch.setattr(main, "generate_method_sql", lambda *args: SAFE_SQL)
    monkeypatch.setattr(main, "fetch_lock_waits", lambda sql: [])
    response = client.post(
        "/diagnose", json={"question": "locks"}, headers=bearer("operator")
    )
    assert response.status_code == 200
    assert response.json()["rows"] == []


@pytest.mark.parametrize(
    "candidate",
    [
        "SELECT waiting_pid, blocking_pid, wait_seconds FROM other_table",
        "SELECT waiting_pid, blocking_pid, wait_seconds FROM diagnostics.lock_waits; DELETE FROM x",
        "SELECT waiting_pid, blocking_pid, pg_sleep(1) FROM diagnostics.lock_waits",
        "SELECT waiting_pid, blocking_pid, wait_seconds FROM diagnostics.lock_waits WHERE 1=1",
    ],
)
def test_unsafe_model_sql_never_reaches_database(monkeypatch, candidate):
    monkeypatch.setattr(main, "generate_method_sql", lambda *args: candidate)

    def fail(sql):
        pytest.fail("Unvalidated SQL reached PostgreSQL")

    monkeypatch.setattr(main, "fetch_lock_waits", fail)
    response = client.post(
        "/diagnose", json={"question": "locks"}, headers=bearer("operator")
    )
    assert response.status_code == 502
    assert response.json() == {"detail": "Model SQL is not allowed"}


def test_untrusted_token_cannot_claim_operator(monkeypatch):
    monkeypatch.setattr(main, "generate_method_sql", lambda *args: pytest.fail("model called"))
    response = client.post(
        "/diagnose", json={"question": "locks"}, headers=bearer("operator", "different-secret-with-at-least-32-chars")
    )
    assert response.status_code == 401


def test_valid_sql_is_replaced_with_fixed_statement():
    assert validate_candidate_sql(
        "select blocking_pid, wait_seconds, waiting_pid from diagnostics.lock_waits;"
    ) == SAFE_SQL


def test_safe_model_sort_and_limit_are_validated_but_not_executed():
    candidate = SAFE_SQL + " ORDER BY wait_seconds DESC LIMIT 2"
    assert validate_candidate_sql(candidate) == SAFE_SQL


def test_simple_duration_filter_is_replaced_by_fixed_long_query_projection():
    schema = CATALOG["long_queries"]
    assert validate_candidate_sql(schema.safe_sql + " WHERE duration_seconds > 25", schema) == schema.safe_sql


@pytest.mark.parametrize("suffix", [
    "WHERE duration_seconds > pg_sleep(1)",
    "WHERE duration_seconds > 25 OR true",
    "WHERE pid > 25",
])
def test_unsafe_long_query_filter_is_rejected(suffix):
    schema = CATALOG["long_queries"]
    with pytest.raises(UnsafeSQL):
        validate_candidate_sql(schema.safe_sql + " " + suffix, schema)


@pytest.mark.parametrize("suffix", [
    "ORDER BY pg_sleep(1) LIMIT 1",
    "ORDER BY wait_seconds LIMIT 1000",
    "ORDER BY wait_seconds LIMIT -1",
    "ORDER BY wait_seconds LIMIT 2 OFFSET 1",
    "WHERE wait_seconds > 0 ORDER BY wait_seconds LIMIT 2",
])
def test_unsafe_sort_or_limit_is_rejected(suffix):
    with pytest.raises(UnsafeSQL):
        validate_candidate_sql(SAFE_SQL + " " + suffix)


def test_fenced_star_is_expanded_to_fixed_projection(monkeypatch):
    monkeypatch.setattr(
        main, "generate_method_sql",
        lambda *args: "```sql\nSELECT * FROM diagnostics.lock_waits;\n```",
    )
    executed = []
    monkeypatch.setattr(main, "fetch_lock_waits", lambda sql: executed.append(sql) or [])
    response = client.post(
        "/diagnose", json={"question": "Hangi oturumlar kilit bekliyor?"},
        headers=bearer("operator"),
    )
    assert response.status_code == 200
    assert executed == [SAFE_SQL]


@pytest.mark.parametrize("candidate", [
    "SELECT * FROM diagnostics.lock_waits WHERE true",
    "SELECT * FROM diagnostics.lock_waits JOIN pg_catalog.pg_stat_activity ON true",
    "```sql\nSELECT * FROM diagnostics.lock_waits; DELETE FROM x;\n```",
])
def test_star_normalization_does_not_allow_extra_sql(candidate):
    with pytest.raises(UnsafeSQL):
        validate_candidate_sql(candidate)


def test_extra_statement_is_rejected():
    with pytest.raises(UnsafeSQL):
        validate_candidate_sql(SAFE_SQL + "; SELECT 1")
