"""Measure offline validator decisions on the same labeled candidate list."""

import argparse
import json
from pathlib import Path

from api.app.offline_validator import validate_record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidates", type=Path)
    parser.add_argument("--output", type=Path, default=Path("evaluation/output/a2.json"))
    args = parser.parse_args()
    decisions = []
    for line in args.candidates.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if type(record.get("expected_allowed")) is not bool:
            parser.error(f"{record.get('candidate_id')}: expected_allowed must be true or false")
        decisions.append({
            **validate_record(record), "expected_allowed": record["expected_allowed"],
        })
    if not decisions:
        parser.error("No labeled candidates")
    permitted = [record for record in decisions if record["expected_allowed"]]
    forbidden = [record for record in decisions if not record["expected_allowed"]]
    blocked = sum(not record["accepted"] for record in forbidden)
    false_blocked = sum(not record["accepted"] for record in permitted)
    summary = {
        "allowed_n": len(permitted), "unauthorized_n": len(forbidden),
        "unfiltered": {"unauthorized_blocked": 0, "allowed_false_blocked": 0},
        "validated": {
            "unauthorized_blocked": blocked, "allowed_false_blocked": false_blocked,
            "unauthorized_block_pct": 100 * blocked / len(forbidden) if forbidden else None,
            "false_block_pct": 100 * false_blocked / len(permitted) if permitted else None,
        },
        "decisions": decisions,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
