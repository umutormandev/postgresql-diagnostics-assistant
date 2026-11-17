"""Regression checks for paired analysis and Clarification scoring."""

from evaluation.report import paired_comparison
from evaluation.run import evaluate_case
import httpx


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
