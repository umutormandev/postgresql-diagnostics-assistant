"""Record PostgreSQL plan and execution times for a read-only catalog query."""

import argparse
import json
import os
from pathlib import Path

import psycopg

from api.app.catalog import CATALOG


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("intent", choices=CATALOG)
    parser.add_argument("--output", type=Path, default=Path("evaluation/output/db-plan.json"))
    args = parser.parse_args()
    schema = CATALOG[args.intent]
    url = os.getenv("DIAGNOSTICS_DATABASE_URL")
    if not url:
        parser.error("Set DIAGNOSTICS_DATABASE_URL to the restricted database role")
    if not schema.live:
        parser.error("This diagnostic view is provisional")
    with psycopg.connect(url, connect_timeout=5) as connection:
        connection.read_only = True
        with connection.transaction():
            connection.execute("SELECT set_config('statement_timeout', %s, true)", ("3000ms",))
            document = connection.execute(
                f"EXPLAIN (ANALYZE, FORMAT JSON) {schema.safe_sql}"
            ).fetchone()[0][0]
    result = {
        "intent": schema.intent, "view": schema.view,
        "planning_ms": document.get("Planning Time"),
        "execution_ms": document.get("Execution Time"),
        "plan": document["Plan"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
