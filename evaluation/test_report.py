"""Regression checks for paired analysis and Clarification scoring."""

from evaluation.report import paired_comparison
import httpx
import pytest

from evaluation.run import evaluate_case, reusable_record, row_matches, timed_request


def test_live_rows_match_only_independent_stable_fields():
    assert row_matches(
        [{"pid": 42, "duration_seconds": 39.1, "query_text": "SELECT pg_sleep(90)"}],
        [{"pid": 42, "query_text": "SELECT pg_sleep(90)"}], False,
        ["pid", "query_text"],
    )
    assert not row_matches([{"pid": 43}], [{"pid": 42}], False, ["pid"])


@pytest.mark.parametrize(("patch", "correct"), [
    ({}, True),
    ({"calls": 3}, False),
    ({"total_exec_time_ms": 9.0}, False),
    ({"mean_exec_time_ms": 9.0}, False),
])
def test_live_statistics_checks_identity_order_and_values(patch, correct):
    first_row = {"queryid": 1, "calls": 2, "total_exec_time_ms": 5.0, "mean_exec_time_ms": 2.5}
    latest_row = {**first_row, "queryid": 9007199254740993}
    ranks = iter(({"batch_id": 10, "rows": [first_row]},
                  {"batch_id": 11, "rows": [latest_row]}))

    def send(condition, role, payload):
        return httpx.Response(200, json={
            "status": "ok", "intent": "query_stats",
            "rows": [{**latest_row, "queryid": str(latest_row["queryid"]), **patch}],
        })

    case = {"id": "moving", "kind": "clear", "dataset": "D1", "workload": "W1",
            "environment": "live", "role": "viewer", "question": "Toplam süreye göre ilk sorgu",
            "expected": {"http": 200, "intent": "query_stats", "rows": [{"queryid": 1}],
                         "ordered": True, "match_fields": ["queryid"]}}
    result = evaluate_case(send, case, "b1", 1, lambda _: next(ranks))
    assert result["correct"] is correct
    assert result["reference_before"]["batch_id"] == 10
    assert result["reference_after"]["batch_id"] == 11


def test_denial_with_rows_is_not_a_successful_block():
    case = {"id": "denied", "kind": "denied", "dataset": "D1", "workload": "W1",
            "environment": "live", "role": "viewer", "question": "Kilit bekleyenleri göster",
            "expected": {"http": 403}}
    result = evaluate_case(
        lambda *_: httpx.Response(403, json={"rows": [{"waiting_pid": 12}]}), case, "b1", 1,
    )
    assert result["denied"] is True
    assert result["correct"] is False
    assert result["unauthorized_data"] is True


def test_insufficient_statistics_are_infrastructure_not_wrong_answer():
    result = timed_request(
        lambda *_: httpx.Response(200, json={"status": "insufficient_data", "rows": []}),
        "D1-W1", "viewer", {"question": "istatistik"},
    )
    assert result["stage"] == "insufficient_data"


def test_missing_live_references_exclude_measurement():
    case = {"id": "missing", "kind": "clear", "dataset": "D1", "workload": "W1",
            "environment": "live", "role": "viewer", "question": "Toplam süre",
            "expected": {"http": 200, "intent": "query_stats", "rows": []}}
    result = evaluate_case(
        lambda *_: httpx.Response(200, json={"status": "insufficient_data", "rows": []}),
        case, "b1", 1, lambda _: {"batch_id": None, "queryids": [], "unavailable": True},
    )
    assert result["correct"] is None


def test_resume_preserves_wrong_model_answers_but_retries_infrastructure():
    assert reusable_record({"correct": False, "attempts": [{
        "stage": "validator", "body": {"detail": "Model SQL is not allowed"},
    }]})
    assert not reusable_record({"correct": None, "attempts": [{
        "stage": "database", "body": {},
    }]})
    assert not reusable_record({"correct": False, "attempts": [{
        "body": {"status": "insufficient_data"},
    }]})


def test_compares_case_majorities_instead_of_counting_repeats_as_cases():
    observations = []
    outcomes = {
        "win": {"proposed": [True, True, False], "b3": [False, False, False]},
        "loss": {"proposed": [False, False, True], "b3": [True, True, False]},
        "unavailable": {"proposed": [None, True, True], "b3": [False, False, False]},
    }
    for case, methods in outcomes.items():
        for method, values in methods.items():
            for repetition, correct in enumerate(values, 1):
                observations.append({"kind": "clear", "case_id": case, "method": method,
                                     "repetition": repetition, "correct": correct})
    comparison = paired_comparison(observations, repetitions=3, seed=381)
    assert comparison["paired_cases"] == 2
    assert comparison["proposed_only_correct"] == comparison["b3_only_correct"] == 1
    assert comparison["mcnemar_exact_p"] == 1


def test_ambiguous_case_reuses_question_and_adds_the_expected_metric():
    calls = []

    def send(condition, role, payload):
        calls.append((condition, role, payload))
        if len(calls) == 1:
            return httpx.Response(200, json={"status": "clarification_required", "rows": [],
                                            "clarification": {"options": ["total", "mean"]}})
        return httpx.Response(
            200, headers={"Server-Timing": "db;dur=1.234"},
            json={"status": "ok", "intent": "query_stats", "rows": [{"queryid": 2}]},
        )

    case = {"id": "case", "kind": "ambiguous", "dataset": "D1", "workload": "W1",
            "environment": "live", "role": "viewer", "question": "En pahalı sorgu hangisi?",
            "follow_up": "mean", "expected": {"http": 200, "intent": "query_stats",
                                              "rows": [{"queryid": 2}], "ordered": True}}
    result = evaluate_case(send, case, "b1", 1)
    assert result["correct"] is result["clarification_correct"] is True
    assert calls[1][2] == {**calls[0][2], "metric": "mean"}
    assert result["db_ms"] == 1.234
