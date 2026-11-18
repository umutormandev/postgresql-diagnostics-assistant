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
    monkeypatch.setenv("DIAGNOSTICS_DEMO_MODE", "1")


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
    assert visible_schemas("viewer") == (CATALOG["query_stats"],)
    if intent == "query_stats":
        assert select_schema(question, "viewer").intent == intent
    else:
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


@pytest.mark.parametrize(
    ("question", "expected_metric", "expected_queryid"),
    [
        ("Toplam çalışma süresine göre en pahalı sorgu hangisi?", "total", 901),
        ("Çağrı başına ortalama süreye göre en pahalı sorgu hangisi?", "mean", 902),
    ],
)
def test_explicit_question_metric_needs_no_followup(
    monkeypatch, question, expected_metric, expected_queryid
):
    seen = []

    def candidate(prompt, method, schema, metric):
        seen.append(metric)
        return CATALOG["query_stats"].safe_sql

    monkeypatch.setattr(main, "generate_method_sql", candidate)
    response = client.post(
        "/diagnose", json={"question": question}, headers=bearer("operator")
    )
    assert response.status_code == 200
    assert response.json()["rows"][0]["queryid"] == expected_queryid
    assert seen == [expected_metric]


def test_conflicting_cost_metric_is_rejected(monkeypatch):
    monkeypatch.setattr(main, "generate_method_sql", lambda *args: pytest.fail("model called"))
    response = client.post(
        "/diagnose",
        json={"question": "Toplam süreye göre en pahalı sorgu?", "metric": "mean"},
        headers=bearer("operator"),
    )
    assert response.status_code == 422


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
        0 if method == "b2" else 3
    )


@pytest.mark.parametrize("intent", ["lock_waits", "long_queries", "query_stats"])
def test_proposed_and_a1_change_only_schema_context(intent):
    selected = CATALOG[intent]
    question = "Hangi oturum hangisini engelliyor?"
    proposed = services.build_messages(question, "proposed", selected)
    a1 = services.build_messages(question, "a1", selected)
    fixed_context = "; ".join(schema.describe() for schema in visible_schemas("operator"))
    assert proposed[0]["content"].replace(selected.describe(), "{schema}") == (
        a1[0]["content"].replace(fixed_context, "{schema}")
    )
    assert proposed[1:] == a1[1:]
    assert sum(message["role"] == "assistant" for message in proposed) == 3


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


@pytest.mark.parametrize("method", ["b2", "b3", "a1", "proposed"])
def test_all_methods_keep_existing_lock_response_contract(monkeypatch, method):
    monkeypatch.delenv("DIAGNOSTICS_DEMO_MODE")
    monkeypatch.setattr(
        main, "generate_method_sql", lambda *args: CATALOG["lock_waits"].safe_sql
    )
    monkeypatch.setattr(main, "fetch_lock_waits", lambda sql: [
        {"waiting_pid": 7426, "blocking_pid": 7412, "wait_seconds": 18}
    ])
    response = client.post(
        "/diagnose", json={"question": "Hangi oturum hangisini engelliyor?", "method": method},
        headers=bearer("operator"),
    )
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok", "intent": "lock_waits",
        "rows": [{"waiting_pid": 7426, "blocking_pid": 7412, "wait_seconds": 18}],
        "source": "pg_blocking_pids + pg_locks",
    }


@pytest.mark.parametrize("question,intent", [
    ("Uzun süren sorgular?", "long_queries"),
    ("Toplam süreye göre en pahalı sorgu hangisi?", "query_stats"),
])
def test_normal_api_reports_unavailable_without_model_or_database(
    monkeypatch, question, intent
):
    monkeypatch.delenv("DIAGNOSTICS_DEMO_MODE")
    monkeypatch.setattr(main, "generate_method_sql", lambda *args: pytest.fail("model called"))
    monkeypatch.setattr(main, "fetch_lock_waits", lambda sql: pytest.fail("DB called"))
    response = client.post(
        "/diagnose", json={"question": question}, headers=bearer("operator")
    )
    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable", "intent": intent, "rows": [],
        "source": "unavailable", "detail": "Live diagnostic view is not available",
    }


def test_ambiguous_cost_question_still_clarifies_in_normal_mode(monkeypatch):
    monkeypatch.delenv("DIAGNOSTICS_DEMO_MODE")
    monkeypatch.setattr(main, "generate_method_sql", lambda *args: pytest.fail("model called"))
    response = client.post(
        "/diagnose", json={"question": "En pahalı sorgu hangisi?"},
        headers=bearer("operator"),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "clarification_required"


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


@pytest.mark.parametrize(("phrase", "metric"), [
    ("Toplam süreye göre en pahalı sorgu?", "total"),
    ("Çağrı başına ortalama süreye göre en pahalı sorgu?", "mean"),
])
def test_a2_infers_explicit_cost_metric(phrase, metric):
    result = validate_record({
        "candidate_id": "cost", "method": "a1", "role": "operator",
        "question": phrase, "sql": CATALOG["query_stats"].safe_sql,
    })
    assert result["accepted"] is True
    assert result["metric"] == metric


def test_a2_rejects_non_object_record():
    assert validate_record([]) == {
        "candidate_id": None, "accepted": False,
        "reason": "Candidate record must be a JSON object",
    }


def test_b1_filters_active_queries_without_calling_a_model(monkeypatch):
    monkeypatch.setattr(main, "generate_method_sql", lambda *args: pytest.fail("model called"))
    response = client.post(
        "/diagnose", json={"question": "25 saniyeyi aşan aktif sorguları göster.", "method": "b1"},
        headers=bearer("operator"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["threshold_seconds"] == 25
    assert [row["pid"] for row in body["rows"]] == [7312]


def test_viewer_only_exposes_statistics_in_model_messages(monkeypatch):
    captured = []
    monkeypatch.setattr(services, "_chat_completion", lambda messages: captured.extend(messages)
                        or CATALOG["query_stats"].safe_sql)
    response = client.post(
        "/diagnose", json={"question": "Son 15 dakikada toplam süreye göre ilk 2 sorguyu göster.",
                          "method": "b3"},
        headers=bearer("viewer"),
    )
    assert response.status_code == 200
    assert [row["queryid"] for row in response.json()["rows"]] == [901, 902]
    assert "diagnostics.query_stats" in json.dumps(captured)
    assert "diagnostics.lock_waits" not in json.dumps(captured)
    assert "diagnostics.long_queries" not in json.dumps(captured)
