import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main, services
from app.catalog import CATALOG, select_schema, visible_schemas
from app.offline_validator import validate_record
from test_diagnose import bearer


client = TestClient(main.app)


@pytest.fixture(autouse=True)
def auth_secret(monkeypatch):
    monkeypatch.setenv("DIAGNOSTICS_AUTH_SECRET", "test-secret-with-at-least-thirty-two-characters")


@pytest.mark.parametrize(
    ("question", "intent"),
    [
        ("Hangi oturum hangisini engelliyor?", "lock_waits"),
        ("Uzun süren sorgular hangileri?", "long_queries"),
        ("En pahalı sorgu hangisi?", "query_stats"),
    ],
)
def test_turkish_question_selects_schema(question, intent):
    assert select_schema(question, "operator").intent == intent
    assert visible_schemas("viewer") == ()
    with pytest.raises(PermissionError):
        select_schema(question, "viewer")


def test_cost_question_requests_metric_before_model(monkeypatch):
    monkeypatch.setattr(main, "generate_method_sql", lambda *args: pytest.fail("model called"))
    response = client.post(
        "/diagnose", json={"question": "En pahalı sorgu hangisi?"},
        headers=bearer("operator"),
    )
    assert response.status_code == 200
    assert response.json() == {
        "status": "clarification_required", "intent": "query_stats", "rows": [],
        "source": "schema_catalog",
        "clarification": {
            "question": "Toplam çalışma süresi mi, çağrı başına ortalama süre mi?",
            "options": ["total", "mean"],
        },
    }


@pytest.mark.parametrize(("metric", "expected_queryid"), [("total", 901), ("mean", 902)])
def test_cost_metrics_choose_different_rows(monkeypatch, metric, expected_queryid):
    monkeypatch.setattr(
        main, "generate_method_sql", lambda *args: CATALOG["query_stats"].safe_sql
    )
    monkeypatch.setattr(main, "fetch_lock_waits", lambda sql: pytest.fail("DB called"))
    response = client.post(
        "/diagnose", json={"question": "En pahalı sorgu hangisi?", "metric": metric},
        headers=bearer("operator"),
    )
    assert response.status_code == 200
    assert response.json()["rows"][0]["queryid"] == expected_queryid
    assert response.json()["source"] == "controlled_fixture"


def test_long_queries_use_controlled_rows(monkeypatch):
    monkeypatch.setattr(
        main, "generate_method_sql", lambda *args: CATALOG["long_queries"].safe_sql
    )
    response = client.post(
        "/diagnose", json={"question": "Uzun süren sorgular?"},
        headers=bearer("operator"),
    )
    assert response.status_code == 200
    assert response.json()["intent"] == "long_queries"
    assert response.json()["rows"] == [
        {"pid": 7312, "duration_seconds": 42, "query_text": "SELECT * FROM orders"}
    ]


@pytest.mark.parametrize("method", ["b2", "b3", "a1", "proposed"])
def test_methods_share_hf_settings_and_expected_schema_context(monkeypatch, method):
    requests = []

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": CATALOG["lock_waits"].safe_sql}}]}

    class FakeClient:
        def __init__(self, timeout):
            assert timeout == 15.0

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def post(self, url, headers, json):
            requests.append(json)
            assert headers["Authorization"] == "Bearer controlled-token"
            return FakeResponse()

    monkeypatch.setenv("HF_TOKEN", "controlled-token")
    monkeypatch.setenv("HF_MODEL", "controlled/model")
    monkeypatch.setattr(services.httpx, "Client", FakeClient)
    result = services.generate_method_sql(
        "Hangi oturum hangisini engelliyor?", method, CATALOG["lock_waits"]
    )
    assert result == CATALOG["lock_waits"].safe_sql
    request = requests[0]
    assert (request["model"], request["max_tokens"], request["temperature"]) == (
        "controlled/model", 128, 0,
    )
    system = request["messages"][0]["content"]
    assert "diagnostics.lock_waits" in system
    assert ("diagnostics.long_queries" in system) is (method != "proposed")
    assert sum(message["role"] == "assistant" for message in request["messages"]) == (
        3 if method == "b3" else 0
    )


@pytest.mark.parametrize("method", ["b2", "b3", "a1", "proposed"])
def test_all_methods_are_callable_through_api(monkeypatch, method):
    monkeypatch.setattr(
        main, "generate_method_sql", lambda *args: CATALOG["long_queries"].safe_sql
    )
    response = client.post(
        "/diagnose", json={"question": "Uzun süren sorgular?", "method": method},
        headers=bearer("operator"),
    )
    assert response.status_code == 200
    assert response.json()["intent"] == "long_queries"


def test_selected_schema_rejects_other_view_before_fixture(monkeypatch):
    monkeypatch.setattr(
        main, "generate_method_sql", lambda *args: CATALOG["lock_waits"].safe_sql
    )
    response = client.post(
        "/diagnose", json={"question": "Uzun süren sorgular?", "method": "b2"},
        headers=bearer("operator"),
    )
    assert response.status_code == 502


def test_a2_replays_same_recorded_candidates_offline():
    path = Path(__file__).resolve().parents[1] / "examples" / "recorded_candidates.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    lock_records = records[:4]
    assert len({record["sql"] for record in lock_records}) == 1
    assert {record["method"] for record in lock_records} == {"proposed", "b2", "b3", "a1"}
    results = [validate_record(record) for record in records]
    assert [result["accepted"] for result in results] == [True] * 6 + [False]
    assert results[-1]["reason"]


def test_a2_denies_viewer_record():
    result = validate_record({
        "candidate_id": "viewer", "method": "b2", "role": "viewer",
        "question": "Kilit beklemeleri?", "sql": CATALOG["lock_waits"].safe_sql,
    })
    assert result["accepted"] is False


def test_a2_rejects_non_object_record():
    assert validate_record([]) == {
        "candidate_id": None, "accepted": False,
        "reason": "Candidate record must be a JSON object",
    }
