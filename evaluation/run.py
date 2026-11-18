"""Run a reproducible diagnostics benchmark against fixtures or team API servers."""

import argparse
from collections import Counter
from collections.abc import Callable
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import random
import re
import secrets
from time import perf_counter
from unittest.mock import patch

import httpx

from .fixtures import CASES, ROLES, load_snapshots, signed_test_token


METHODS = ("b1", "b2", "b3", "proposed", "a1")
Send = Callable[[str, str, dict], httpx.Response]


def load_cases(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows or len({case["id"] for case in rows}) != len(rows):
        raise ValueError("At least one case and unique case IDs are required")
    for case in rows:
        if case["kind"] == "ambiguous" and case.get("follow_up") not in ("total", "mean"):
            raise ValueError(f"{case['id']}: ambiguous cases need a standard follow-up")
    return rows


def timed_request(send: Send, condition: str, role: str, payload: dict) -> dict:
    started = perf_counter()
    try:
        response = send(condition, role, payload)
    except httpx.RequestError as error:
        return {"http": None, "body": {}, "stage": "transport", "error": type(error).__name__,
                "api_ms": (perf_counter() - started) * 1000, "db_ms": None}
    elapsed = (perf_counter() - started) * 1000
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    status = response.status_code
    stage = response.headers.get("X-Diagnostics-Stage")
    if stage is None:
        stage = {401: "authentication", 403: "policy", 422: "scope",
                 502: "validator", 503: "unavailable"}.get(status)
    if stage is None and body.get("status") == "insufficient_data":
        stage = "insufficient_data"
    if stage is None and (status >= 500 or not body):
        stage = "transport"
    timing = re.search(r"(?:^|,)\s*db;dur=([\d.]+)", response.headers.get("Server-Timing", ""))
    return {
        "http": status, "body": body, "stage": stage, "api_ms": elapsed,
        "db_ms": float(timing[1]) if timing else None,
    }


def row_matches(actual: list, expected: list, ordered: bool,
                match_fields: list[str] | None = None) -> bool:
    if match_fields is not None:
        if any(not isinstance(row, dict) or any(key not in row for key in match_fields)
               for row in actual + expected):
            return False
        actual = [{key: row[key] for key in match_fields} for row in actual]
        expected = [{key: row[key] for key in match_fields} for row in expected]
    if ordered:
        return actual == expected
    try:
        return Counter(tuple(sorted(row.items())) for row in actual) == Counter(
            tuple(sorted(row.items())) for row in expected
        )
    except (AttributeError, TypeError):
        return False


def reusable_record(record: dict) -> bool:
    """Keep wrong model answers; retry only incomplete infrastructure observations."""
    return record.get("correct") is not None and not any(
        attempt.get("body", {}).get("status") == "insufficient_data"
        for attempt in record.get("attempts", [])
    )


def evaluate_case(send: Send, case: dict, method: str, repetition: int,
                  live_reference: Callable[[dict], dict] | None = None) -> dict:
    condition = f"{case['dataset']}-{case['workload']}"
    dynamic_stats = (live_reference is not None and case["kind"] != "denied"
                     and case["expected"]["intent"] == "query_stats")
    reference_before = live_reference(case) if dynamic_stats else None
    payload = {"question": case["question"], "method": method}
    first = timed_request(send, condition, case["role"], payload)
    attempts = [first]
    clarification = first["body"].get("clarification", {})
    clarified = None
    if case["kind"] == "ambiguous":
        clarified = (
            first["http"] == 200 and first["body"].get("status") == "clarification_required"
            and set(clarification.get("options", [])) == {"total", "mean"}
            and first["body"].get("rows") == []
        )
        if clarified:
            attempts.append(timed_request(send, condition, case["role"], {
                **payload, "metric": case["follow_up"],
            }))
    last = attempts[-1]
    reference_after = live_reference(case) if dynamic_stats else None
    expected = case["expected"]
    actual = last["body"]
    infra = any(item["stage"] in ("transport", "model", "database", "authentication",
                                  "unavailable", "insufficient_data")
                for item in attempts)
    available_references = ([item for item in (reference_before, reference_after)
                             if item is not None and not item.get("unavailable")]
                            if dynamic_stats else [])
    if dynamic_stats and not available_references:
        infra = True
    if expected["http"] == 403:
        correct = last["http"] == 403
    else:
        matched_rows = row_matches(actual.get("rows", []), expected["rows"],
                                   expected.get("ordered", False), expected.get("match_fields"))
        if dynamic_stats and isinstance(actual.get("rows"), list):
            actual_ids = [row.get("queryid") for row in actual["rows"]]
            matched_rows = any(actual_ids == item["queryids"]
                               for item in available_references)
        correct = (
            last["http"] == expected["http"] and actual.get("status") == expected.get("status", "ok")
            and actual.get("intent") == expected["intent"]
            and isinstance(actual.get("rows"), list)
            and matched_rows
            and clarified is not False
        )
    db_times = [item["db_ms"] for item in attempts if item["db_ms"] is not None]
    return {
        "case_id": case["id"], "condition": condition, "dataset": case["dataset"],
        "workload": case["workload"], "role": case["role"], "kind": case["kind"],
        "environment": case["environment"], "method": method, "repetition": repetition,
        "correct": None if infra else correct, "clarification_correct": None if infra else clarified,
        "denied": last["http"] == 403, "denial_stage": last["stage"],
        "unauthorized_data": expected["http"] == 403 and any(
            bool(item["body"].get("rows")) for item in attempts
        ),
        "api_ms": sum(item["api_ms"] for item in attempts),
        "db_ms": sum(db_times) if db_times else None,
        "attempts": attempts,
        "reference_before": reference_before,
        "reference_after": reference_after,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fixture", action="store_true", help="Use controlled snapshots with B1")
    source.add_argument("--targets", type=Path, help="JSON object mapping conditions to base URLs")
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--methods", nargs="+", choices=METHODS)
    parser.add_argument("--case-ids", nargs="+", help="Run only these case IDs for a smoke test")
    parser.add_argument("--exclude-intents", nargs="+", choices=("lock_waits", "long_queries", "query_stats"),
                        help="Skip these permitted intents while an environment recovers")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--seed", type=int, default=381)
    parser.add_argument("--output", type=Path, default=Path("evaluation/output/benchmark.jsonl"))
    parser.add_argument("--model", help="Exact provider/model/version for a live LLM run")
    parser.add_argument("--live-reference-config", type=Path,
                        help="Refresh statistics ranks from independent PostgreSQL snapshots per call")
    parser.add_argument("--resume-from", type=Path, nargs="+",
                        help="Reuse completed valid measurements from an interrupted JSONL run")
    args = parser.parse_args()
    methods = args.methods or (["b1"] if args.fixture else METHODS)
    cases = load_cases(args.cases)
    if args.case_ids:
        unknown = set(args.case_ids) - {case["id"] for case in cases}
        if unknown:
            parser.error(f"Unknown case IDs: {', '.join(sorted(unknown))}")
        cases = [case for case in cases if case["id"] in args.case_ids]
    if args.exclude_intents:
        cases = [case for case in cases if case["kind"] == "denied"
                 or case["expected"]["intent"] not in args.exclude_intents]
    if not cases:
        parser.error("No cases remain after filtering")
    environment = "fixture" if args.fixture else "live"
    conditions = {f"{case['dataset']}-{case['workload']}" for case in cases}
    if args.repetitions < 1 or len(methods) != len(set(methods)):
        parser.error("Use a positive repetition count and distinct methods")
    if any(case["environment"] != environment for case in cases):
        parser.error(f"Run {environment} cases against the corresponding environment")
    if args.fixture and methods != ["b1"]:
        parser.error("Controlled snapshots run B1 only")
    if args.fixture and args.live_reference_config:
        parser.error("Live reference configuration requires --targets")
    if args.resume_from and args.fixture:
        parser.error("Resume is only supported for live runs")
    if not args.fixture and any(method != "b1" for method in methods) and not args.model:
        parser.error("Provide --model with the live LLM model and version")

    with ExitStack() as stack:
        live_reference = None
        existing = {}
        if args.resume_from:
            for previous in args.resume_from:
                for line in previous.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    if not reusable_record(record):
                        continue
                    key = (record["case_id"], record["method"], record["repetition"])
                    existing[key] = record
            wanted_ids = {case["id"] for case in cases}
            existing = {key: value for key, value in existing.items()
                        if key[0] in wanted_ids and key[1] in methods
                        and 1 <= key[2] <= args.repetitions}
        if args.fixture:
            from fastapi.testclient import TestClient
            from api.app.main import app

            secret = secrets.token_hex(32)
            snapshots = load_snapshots()
            stack.enter_context(patch.dict(os.environ, {"DIAGNOSTICS_AUTH_SECRET": secret}))
            client = stack.enter_context(TestClient(app))
            tokens = {role: signed_test_token(role, secret) for role in ROLES}

            def send(condition, role, payload):
                app.state.controlled_fixture = snapshots[condition]
                return client.post("/diagnose", json=payload, headers={
                    "Authorization": f"Bearer {tokens[role]}",
                })
        else:
            targets = json.loads(args.targets.read_text(encoding="utf-8"))
            tokens = {role: os.getenv(f"DIAGNOSTICS_{role.upper()}_JWT") for role in ROLES}
            if missing := conditions - targets.keys():
                parser.error(f"Missing API URLs for {', '.join(sorted(missing))}")
            if any(not tokens[case["role"]] for case in cases):
                parser.error("Set the signed DIAGNOSTICS_OPERATOR_JWT and DIAGNOSTICS_VIEWER_JWT used by your cases")
            client = stack.enter_context(httpx.Client(timeout=255.0, trust_env=False))
            if args.live_reference_config:
                from .live_lab import live_stats_reference
                reference_config = json.loads(args.live_reference_config.read_text(encoding="utf-8"))
                live_reference = lambda case: live_stats_reference(reference_config, case)

            def send(condition, role, payload):
                return client.post(targets[condition].rstrip("/") + "/diagnose", json=payload,
                                   headers={"Authorization": f"Bearer {tokens[role]}"})

        args.output.parent.mkdir(parents=True, exist_ok=True)
        rng = random.Random(args.seed)
        metadata = {
            "environment": environment, "cases": str(args.cases), "case_count": len(cases),
            "methods": methods, "repetitions": args.repetitions, "seed": args.seed,
            "model": args.model, "platform": platform.platform(), "python": platform.python_version(),
            "live_reference_config": str(args.live_reference_config) if args.live_reference_config else None,
            "resume_from": [str(path) for path in args.resume_from] if args.resume_from else None,
            "reused_measurements": len(existing),
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        args.output.with_suffix(".meta.json").write_text(
            json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
        )
        count = 0
        with args.output.open("w", encoding="utf-8") as output:
            for repetition in range(1, args.repetitions + 1):
                schedule = [(case, method) for case in cases for method in methods]
                rng.shuffle(schedule)
                for case, method in schedule:
                    key = (case["id"], method, repetition)
                    record = existing.get(key)
                    if record is None:
                        record = evaluate_case(send, case, method, repetition, live_reference)
                    output.write(json.dumps(record, ensure_ascii=False) + "\n")
                    output.flush()
                    count += 1
        print(f"{count} measurements · {environment} · {args.output}")


if __name__ == "__main__":
    main()
