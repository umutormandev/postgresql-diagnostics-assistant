"""Export an auditable benchmark without URLs, credentials or raw SQL."""

import argparse
from hashlib import sha256
from itertools import product
import json
from pathlib import Path

from .run import METHODS, load_cases


METRICS = (
    "case_id", "condition", "dataset", "workload", "role", "kind", "environment",
    "method", "repetition", "correct", "clarification_correct", "denied",
    "denial_stage", "unauthorized_data", "api_ms", "db_ms",
)
RESULT_FIELDS = (
    "queryid", "calls", "total_exec_time_ms", "mean_exec_time_ms",
    "pid", "waiting_pid", "blocking_pid", "wait_seconds", "duration_seconds",
)


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def public_rows(rows: list[dict]) -> list[dict]:
    export = []
    for row in rows:
        item = {field: row[field] for field in RESULT_FIELDS if field in row}
        if "query_text" in row and isinstance(row["query_text"], str):
            item["query_text_sha256"] = sha256(row["query_text"].encode()).hexdigest()
        export.append(item)
    return export


def public_reference(reference: dict | None) -> dict | None:
    if reference is None:
        return None
    item = {key: reference[key] for key in ("batch_id", "baseline_batch_id", "unavailable", "queryids")
            if key in reference}
    if isinstance(reference.get("rows"), list):
        item["rows"] = public_rows(reference["rows"])
    return item


def public_measurement(record: dict) -> dict:
    item = {field: record[field] for field in METRICS}
    for field in ("reference_before", "reference_after"):
        item[field] = public_reference(record.get(field))
    results = []
    for attempt in record.get("attempts", []):
        body = attempt.get("body", {})
        result = {field: attempt.get(field) for field in ("http", "stage", "api_ms", "db_ms")}
        result["status"] = body.get("status")
        result["intent"] = body.get("intent")
        result["rows"] = public_rows(body.get("rows", [])) if isinstance(body.get("rows"), list) else []
        results.append(result)
    item["attempts"] = results
    return item


def write_jsonl(path: Path, objects: list[dict]) -> None:
    path.write_text("".join(json.dumps(o, ensure_ascii=False) + "\n" for o in objects),
                    encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("records", type=Path)
    parser.add_argument("cases", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--records-sha256")
    parser.add_argument("--cases-sha256")
    parser.add_argument("--seed", type=int, help="Recorded seed if the original sidecar is unavailable")
    parser.add_argument("--repetitions", type=int, help="Recorded repetitions if the original sidecar is unavailable")
    args = parser.parse_args()
    source_hashes = {}
    for label, path, expected in (("records", args.records, args.records_sha256),
                                  ("cases", args.cases, args.cases_sha256)):
        source_hashes[label] = digest(path)
        if expected and source_hashes[label] != expected.lower():
            parser.error(f"{label} SHA-256 differs from the recorded experiment")

    sidecar = args.records.with_suffix(".meta.json")
    if sidecar.exists():
        metadata = json.loads(sidecar.read_text(encoding="utf-8"))
        seed, repetitions = metadata["seed"], metadata["repetitions"]
    elif args.seed is not None and args.repetitions is not None:
        metadata = {}
        seed, repetitions = args.seed, args.repetitions
    else:
        parser.error("Provide the run sidecar or its recorded seed and repetition count")

    records = [json.loads(line) for line in args.records.read_text(encoding="utf-8").splitlines()
               if line.strip()]
    cases = {case["id"]: case for case in load_cases(args.cases)}
    methods = metadata.get("methods") or sorted({record["method"] for record in records})
    if not records or repetitions < 1 or not methods or not set(methods) <= set(METHODS):
        parser.error("The scope of the run is invalid")
    keys = {(r["case_id"], r["method"], r["repetition"]) for r in records}
    expected_keys = set(product(cases, methods, range(1, repetitions + 1)))
    if len(keys) != len(records) or keys != expected_keys:
        parser.error("Measurements contain missing, duplicate or extra case/method/repetition keys")
    for record in records:
        case = cases[record["case_id"]]
        if any(record[field] != case[field]
               for field in ("dataset", "workload", "role", "kind", "environment")):
            parser.error(f"Measurement metadata does not match case {case['id']}")

    args.output.mkdir(parents=True, exist_ok=True)
    data_file = args.output / "measurements.jsonl"
    write_jsonl(data_file, [public_measurement(r) for r in records])
    case_file = args.output / "cases.jsonl"
    write_jsonl(case_file, [
        {"id": c["id"], **{k: c[k] for k in ("dataset", "workload", "role", "kind", "environment")},
         "expected": {k: c["expected"][k] for k in ("http", "intent", "status", "match_fields", "ordered")
                      if k in c["expected"]},
         "reference_rows": public_rows(c["expected"].get("rows", []))}
        for c in cases.values()
    ])
    data_file.with_suffix(".meta.json").write_text(
        json.dumps({"seed": seed, "repetitions": repetitions, "methods": methods,
                    "scoring": metadata.get("scoring", "legacy-result-identity-order"),
                    "case_count": len(cases)}, indent=2) + "\n", encoding="utf-8",
    )
    (args.output / "manifest.json").write_text(json.dumps({
        "source_sha256": source_hashes,
        "export_sha256": {path.name: digest(path) for path in (data_file, case_file,
                                                              data_file.with_suffix(".meta.json"))},
        "measurement_count": len(records),
        "valid_measurements": sum(record["correct"] is not None for record in records),
    }, indent=2) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
