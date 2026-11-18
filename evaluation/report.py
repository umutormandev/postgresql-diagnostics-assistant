"""Summarize benchmark records and compare Proposed with B3 by paired case."""

import argparse
from collections import Counter, defaultdict
import csv
import json
from math import ceil, comb
from pathlib import Path
import random
from statistics import mean, median


def milliseconds(records: list[dict], field: str) -> list[float]:
    return sorted(record[field] for record in records if record[field] is not None)


def rate(successes: list[bool]) -> float | None:
    return 100 * mean(successes) if successes else None


def describe(records: list[dict]) -> dict:
    eligible = [record for record in records if record["correct"] is not None]
    allowed = [record for record in eligible if record["kind"] != "denied"]
    denied = [record for record in eligible if record["kind"] == "denied"]
    ambiguous = [record for record in allowed if record["kind"] == "ambiguous"]
    clarified = [record for record in ambiguous if record["clarification_correct"]]
    api_times = milliseconds(eligible, "api_ms")
    db_times = milliseconds(eligible, "db_ms")
    return {
        "records": len(records), "infrastructure_errors": len(records) - len(eligible),
        "allowed_n": len(allowed), "correct_n": sum(record["correct"] for record in allowed),
        "accuracy_pct": rate([record["correct"] for record in allowed]),
        "clarification_pct": rate([record["clarification_correct"] for record in ambiguous]),
        "follow_up_pct": rate([record["correct"] for record in clarified]),
        "denied_n": len(denied), "block_pct": rate([record["denied"] for record in denied]),
        "false_block_pct": rate([record["denial_stage"] == "policy" or
                                 record["denial_stage"] == "validator" or record["denied"]
                                 for record in allowed]),
        "policy_denials": sum(record["denial_stage"] == "policy" for record in denied),
        "database_denials": sum(record["denial_stage"] == "database_denied" for record in denied),
        "unauthorized_data": sum(record["unauthorized_data"] for record in denied),
        "api_median_ms": median(api_times) if api_times else None,
        "api_p95_ms": api_times[ceil(.95 * len(api_times)) - 1] if api_times else None,
        "db_median_ms": median(db_times) if db_times else None,
    }


def paired_comparison(records: list[dict], repetitions: int, seed: int) -> dict | None:
    by_method = defaultdict(dict)
    for record in records:
        if record["kind"] == "denied" or record["method"] not in ("proposed", "b3"):
            continue
        observations = by_method[(record["case_id"], record["method"])]
        if record["repetition"] in observations:
            raise ValueError(f"Duplicate observation for {record['case_id']} and {record['method']}")
        observations[record["repetition"]] = record["correct"]

    def majority(values: dict) -> bool | None:
        if set(values) != set(range(1, repetitions + 1)) or None in values.values():
            return None
        correct = sum(values.values())
        return None if correct * 2 == repetitions else correct * 2 > repetitions

    differences = []
    for case_id in sorted({key[0] for key in by_method}):
        proposed = majority(by_method.get((case_id, "proposed"), {}))
        b3 = majority(by_method.get((case_id, "b3"), {}))
        if proposed is not None and b3 is not None:
            differences.append(int(proposed) - int(b3))
    if not differences:
        return None
    counts = Counter(differences)
    wins, losses = counts[1], counts[-1]
    discordant = wins + losses
    tail = sum(comb(discordant, count) for count in range(min(wins, losses) + 1))
    p_value = min(1.0, 2 * (tail / (2 ** discordant)))

    rng = random.Random(seed)
    samples = sorted(mean(rng.choices(differences, k=len(differences))) * 100 for _ in range(10_000))
    return {
        "paired_cases": len(differences), "proposed_only_correct": wins, "b3_only_correct": losses,
        "accuracy_difference_pp": mean(differences) * 100, "mcnemar_exact_p": p_value,
        "bootstrap_95_pp": [samples[249], samples[9749]],
    }


def cell(value: object) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}" if isinstance(value, float) else str(value)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("records", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("evaluation/output"))
    args = parser.parse_args()
    records = [json.loads(line) for line in args.records.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not records:
        parser.error("No measurement records")
    environments = {record["environment"] for record in records}
    if len(environments) != 1:
        parser.error("Summarize live runs and controlled runs independently")
    environment = next(iter(environments))
    metadata = json.loads(args.records.with_suffix(".meta.json").read_text(encoding="utf-8"))
    methods = sorted({record["method"] for record in records})
    conditions = sorted({record["condition"] for record in records})
    table = []
    for condition in ("all", *conditions):
        for method in methods:
            selected = [record for record in records if record["method"] == method
                        and (condition == "all" or record["condition"] == condition)]
            if selected:
                table.append({"condition": condition, "method": method, **describe(selected)})
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / "summary.csv").open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(table[0]))
        writer.writeheader()
        writer.writerows(table)

    columns = {
        "method": "Yöntem", "records": "Ölçüm", "correct_n": "Doğru",
        "allowed_n": "İzinli vaka", "accuracy_pct": "Doğruluk (%)",
        "clarification_pct": "Netleştirme (%)", "follow_up_pct": "Takip (%)",
        "block_pct": "Engelleme (%)", "false_block_pct": "Yanlış engel (%)",
        "unauthorized_data": "Yetkisiz veri", "api_median_ms": "Medyan (ms)",
        "api_p95_ms": "P95 (ms)", "db_median_ms": "DB (ms)",
    }
    lines = [
        "# PostgreSQL Teşhis Deneyi",
        "",
        f"Ortam: {'Sentetik API doğrulaması' if environment == 'fixture' else 'Canlı API'}",
        f"Tekrar: {metadata['repetitions']} · Tohum: {metadata['seed']}",
        "",
        "| " + " | ".join(columns.values()) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in table:
        if row["condition"] == "all":
            lines.append("| " + " | ".join(cell(row[key]) for key in columns) + " |")
    skipped = sum(row["infrastructure_errors"] for row in table if row["condition"] == "all")
    lines.extend(["", f"Altyapı hatası nedeniyle ölçüme alınmayan vaka çalıştırması: {skipped}."])
    comparison = paired_comparison(records, metadata["repetitions"], metadata["seed"])
    if comparison:
        low, high = comparison["bootstrap_95_pp"]
        lines.extend([
            "", "## Önerilen Yöntem ve B3",
            "",
            f"{comparison['paired_cases']} eşleşmiş izinli vaka. Doğruluk farkı "
            f"{comparison['accuracy_difference_pp']:.2f} yüzde puan "
            f"(%95 eşleştirilmiş bootstrap aralığı {low:.2f} ile {high:.2f}). "
            f"Exact McNemar p = {comparison['mcnemar_exact_p']:.6g}. "
            f"Yalnız önerilen yöntem doğru: {comparison['proposed_only_correct']}. "
            f"Yalnız B3 doğru: {comparison['b3_only_correct']}.",
        ])
    else:
        lines.extend(["", "Eşleştirilmiş karşılaştırma için tamamlanmış önerilen yöntem ve B3 vakaları gerekiyor."])
    (args.output_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(args.output_dir / "summary.md")


if __name__ == "__main__":
    main()
