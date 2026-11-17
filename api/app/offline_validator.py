"""A2: replay recorded SQL candidates without model or database access."""

import argparse
import json
from pathlib import Path

from .catalog import needs_cost_clarification, resolve_cost_metric, select_schema
from .sql_guard import UnsafeSQL, validate_candidate_sql


METHODS = {"proposed", "b2", "b3", "a1"}


def validate_record(record: dict) -> dict:
    if not isinstance(record, dict):
        return {"candidate_id": None, "accepted": False,
                "reason": "Candidate record must be a JSON object"}
    result = {
        "candidate_id": record.get("candidate_id"),
        "method": record.get("method"),
        "accepted": False,
    }
    try:
        if record.get("method") not in METHODS:
            raise ValueError("Unknown method")
        question = record["question"]
        role = record["role"]
        candidate = record["sql"]
        metric = record.get("metric")
        if not all(isinstance(value, str) for value in (question, role, candidate)):
            raise ValueError("Question, role and SQL must be strings")
        if metric not in (None, "total", "mean"):
            raise ValueError("Unknown metric")
        schema = select_schema(question, role)
        result["intent"] = schema.intent
        metric = resolve_cost_metric(question, schema, metric)
        if needs_cost_clarification(question, schema, metric):
            raise ValueError("Cost metric clarification required")
        if metric:
            result["metric"] = metric
        result["normalized_sql"] = validate_candidate_sql(candidate, schema)
        result["accepted"] = True
    except (KeyError, ValueError, PermissionError, UnsafeSQL) as exc:
        result["reason"] = str(exc)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="A2 offline SQL candidate validator")
    parser.add_argument("candidates", type=Path, help="Recorded JSONL candidates")
    args = parser.parse_args()
    with args.candidates.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                result = validate_record(record)
            except (json.JSONDecodeError, TypeError) as exc:
                result = {"candidate_id": None, "accepted": False,
                          "reason": f"Line {line_number}: invalid JSON record: {exc}"}
            print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
