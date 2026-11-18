"""Take one timestamped pg_stat_statements snapshot per configured database."""

import os

import psycopg


def main() -> None:
    database_url = os.environ["DIAGNOSTICS_SAMPLER_DATABASE_URL"]
    with psycopg.connect(database_url, autocommit=True, connect_timeout=5) as connection:
        batch_id = connection.execute(
            "SELECT diagnostics.capture_query_stats()"
        ).fetchone()[0]
    print(f"capture_batch={batch_id}")


if __name__ == "__main__":
    main()
