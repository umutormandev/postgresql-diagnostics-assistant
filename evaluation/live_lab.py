"""Prepare four isolated PostgreSQL 17 conditions and independent live references.

The generated configuration contains local credentials and belongs in ignored output/.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from .fixtures import signed_test_token

def load_cases(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "evaluation" / "output"
CONFIG = OUTPUT / "live_lab.json"
CONDITIONS = ("D1-W1", "D1-W2", "D2-W1", "D2-W2")
SCHEMA_FILES = sorted((ROOT / "api" / "sql").glob("[0-9]*.sql"))


def save(config):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def load():
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def rotate_credentials(config):
    for entry in config["conditions"].values():
        with psycopg.connect(entry["admin_url"], autocommit=True) as admin:
            for role in ("operator", "viewer", "sampler"):
                key = f"{role}_url"
                username = conninfo_to_dict(entry[key])["user"]
                password = secrets.token_urlsafe(30)
                admin.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                    sql.Identifier(username), sql.Literal(password)))
                entry[key] = make_conninfo(entry[key], password=password)
    save(config)
    print(f"Rotated separate operator, viewer, and sampler passwords: {CONFIG}")


def setup(admin_url):
    config = {"created_at": datetime.now(timezone.utc).isoformat(), "conditions": {},
              "auth_secret": secrets.token_hex(32)}
    with psycopg.connect(admin_url, autocommit=True) as admin:
        for condition in CONDITIONS:
            code = condition.lower().replace("-", "")
            dbname = "diag_" + code
            roles = {role: f"{code}_{role}" for role in ("operator", "viewer", "sampler")}
            passwords = {role: secrets.token_urlsafe(30) for role in roles}
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
            admin.execute(sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(
                sql.Identifier(dbname)))
            db_url = make_conninfo(admin_url, dbname=dbname)
            with psycopg.connect(db_url, autocommit=True) as db:
                for path in SCHEMA_FILES:
                    db.execute(path.read_text(encoding="utf-8"))
                db.execute("CREATE TABLE products (id integer PRIMARY KEY, name text NOT NULL)")
                db.execute("CREATE TABLE orders (id integer PRIMARY KEY, product_id integer REFERENCES products(id), amount numeric NOT NULL)")
                db.execute("CREATE TABLE lock_target (id integer)")
                db.execute("INSERT INTO products SELECT n, 'product-' || n FROM generate_series(1,100) n")
                distribution = "(n % 100) + 1" if condition.startswith("D1") else \
                    "CASE WHEN n % 10 < 8 THEN (n % 5) + 1 ELSE (n % 95) + 6 END"
                db.execute(f"INSERT INTO orders SELECT n, {distribution}, (n % 500)::numeric / 10 "
                           "FROM generate_series(1,10000) n")
                db.execute("CREATE INDEX orders_product_id_idx ON orders(product_id)")
                db.execute("ANALYZE products")
                db.execute("ANALYZE orders")
            for role, membership in (("operator", "diagnostics_operator_reader"),
                                     ("viewer", "diagnostics_viewer_reader"),
                                     ("sampler", "diagnostics_sampler")):
                admin.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(roles[role]), sql.Literal(passwords[role])))
                admin.execute(sql.SQL("GRANT {} TO {}").format(
                    sql.Identifier(membership), sql.Identifier(roles[role])))
                admin.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(dbname), sql.Identifier(roles[role])))
            config["conditions"][condition] = {
                "database": dbname, "admin_url": db_url,
                **{f"{role}_url": make_conninfo(db_url, user=name, password=passwords[role])
                   for role, name in roles.items()},
            }
    save(config)
    print(f"Created {', '.join(CONDITIONS)}; local connection settings: {CONFIG}")


def capture(config, strict=True):
    for condition, entry in config["conditions"].items():
        try:
            with psycopg.connect(entry["sampler_url"], autocommit=True,
                                 connect_timeout=5) as db:
                batch = db.execute("SELECT diagnostics.capture_query_stats()").fetchone()[0]
                print(f"{condition}: batch {batch}", flush=True)
        except psycopg.Error as error:
            if strict:
                raise
            print(f"{condition}: transient capture error: {error}",
                  file=sys.stderr, flush=True)


def sample_loop(config, interval_seconds, action):
    if interval_seconds < 1:
        raise ValueError("Capture interval must be positive")
    while True:
        try:
            action(config)
        except psycopg.Error as error:
            print(f"Transient PostgreSQL sampling/workload error: {error}",
                  file=sys.stderr, flush=True)
        time.sleep(interval_seconds)


def workload(config, repetitions):
    for condition, entry in config["conditions"].items():
        with psycopg.connect(entry["admin_url"], autocommit=True) as db:
            for n in range(repetitions):
                if condition.endswith("W1"):
                    db.execute("SELECT sum(amount) FROM orders WHERE product_id = %s", ((n % 100) + 1,))
                    if n % 5 == 0:
                        db.execute("SELECT name FROM products WHERE id = %s", ((n % 100) + 1,))
                else:
                    db.execute("SELECT product_id, sum(amount) FROM orders GROUP BY product_id ORDER BY 2 DESC LIMIT 10")
                    if n % 5 == 0:
                        db.execute("SELECT count(*) FROM orders WHERE amount > %s", (n % 100,))
            print(f"{condition}: {repetitions} workload cycles")


def hold(config):
    """Keep a report query active and real lock waits present during reference/run."""
    tasks = []
    stop = threading.Event()

    def sleep_query(entry):
        while not stop.is_set():
            try:
                with psycopg.connect(entry["admin_url"], autocommit=True) as db:
                    db.execute("SELECT pg_sleep(3600), (SELECT count(*) FROM orders)")
            except psycopg.Error:
                if not stop.is_set():
                    raise

    def lock_wait(entry):
        with (psycopg.connect(entry["admin_url"], autocommit=True) as blocker,
              psycopg.connect(entry["admin_url"], autocommit=True) as waiter):
            with blocker.transaction():
                blocker.execute("LOCK TABLE lock_target IN ACCESS EXCLUSIVE MODE")
                print(f"{entry['database']}: blocker PID {blocker.info.backend_pid}, "
                      f"waiter PID {waiter.info.backend_pid}", flush=True)
                waiter.execute("SELECT count(*) FROM lock_target")

    for condition, entry in config["conditions"].items():
        tasks.append(threading.Thread(target=sleep_query, args=(entry,), daemon=True))
        if condition != "D1-W2":
            tasks.append(threading.Thread(target=lock_wait, args=(entry,), daemon=True))
    for task in tasks:
        task.start()
    print("Live sessions active; stop this process after the experiment.", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop.set()


def references(config):
    """Use raw PostgreSQL activity/counter tables, independently of API views."""
    source = ROOT / "evaluation" / "controlled_cases.jsonl"
    cases = load_cases(source)
    by_condition = {}
    for condition, entry in config["conditions"].items():
        with psycopg.connect(entry["admin_url"], autocommit=True) as db:
            dbid = db.execute("SELECT oid FROM pg_database WHERE datname=current_database()").fetchone()[0]
            locks = db.execute("""
                SELECT a.pid, unnest(pg_blocking_pids(a.pid))
                FROM pg_stat_activity a WHERE a.datname=current_database()
            """).fetchall()
            longs = db.execute("""
                SELECT pid, left(query,500), extract(epoch FROM clock_timestamp()-query_start)
                FROM pg_stat_activity
                WHERE datname=current_database() AND state='active'
                  AND backend_type='client backend' AND pid<>pg_backend_pid()
                  AND query_start IS NOT NULL
            """).fetchall()
            batches = db.execute("""
                WITH latest AS (
                    SELECT batch_id, captured_at, stats_reset, dealloc
                    FROM diagnostics.query_capture_batches WHERE dbid=%s
                    ORDER BY captured_at DESC, batch_id DESC LIMIT 1
                )
                SELECT * FROM latest
                UNION ALL
                SELECT batch_id, captured_at, stats_reset, dealloc
                FROM diagnostics.query_capture_batches
                WHERE dbid=%s AND captured_at <=
                    (SELECT captured_at - interval '15 minutes' FROM latest)
                ORDER BY captured_at DESC, batch_id DESC LIMIT 2
            """, (dbid, dbid)).fetchall()
            if len(batches) != 2 or not (15 <= (batches[0][1]-batches[1][1]).total_seconds()/60 <= 17):
                raise ValueError(f"{condition}: two captures 15–17 minutes apart are required")
            if batches[0][2:] != batches[1][2:]:
                raise ValueError(f"{condition}: pg_stat_statements counters were reset")
            stats = db.execute("""
                SELECT cur.queryid,
                       round(sum(cur.total_exec_time_ms - coalesce(old.total_exec_time_ms,0))::numeric,3)::float8,
                       round((sum(cur.total_exec_time_ms - coalesce(old.total_exec_time_ms,0)) /
                             sum(cur.calls - coalesce(old.calls,0)))::numeric,3)::float8,
                       sum(cur.calls - coalesce(old.calls,0))::bigint
                FROM diagnostics.query_counter_snapshots cur
                LEFT JOIN diagnostics.query_counter_snapshots old
                  ON old.batch_id=%s AND old.userid=cur.userid AND old.dbid=cur.dbid
                 AND old.queryid=cur.queryid AND old.toplevel=cur.toplevel
                WHERE cur.batch_id=%s AND cur.calls>coalesce(old.calls,0)
                  AND cur.total_exec_time_ms>=coalesce(old.total_exec_time_ms,0)
                GROUP BY cur.queryid
            """, (batches[1][0], batches[0][0])).fetchall()
            by_condition[condition] = {"locks": locks, "longs": longs, "stats": [
                dict(zip(("queryid", "total_exec_time_ms", "mean_exec_time_ms", "calls"), row))
                for row in stats], "batch_ids": [b[0] for b in batches]}
    output = OUTPUT / "live_cases.jsonl"
    with output.open("w", encoding="utf-8") as out:
        for case in cases:
            condition = f"{case['dataset']}-{case['workload']}"
            data = by_condition[condition]
            item = {**case, "environment": "live"}
            if case["kind"] != "denied":
                expected = dict(case["expected"])
                if expected["intent"] == "lock_waits":
                    expected["rows"] = [dict(waiting_pid=p, blocking_pid=b) for p,b in data["locks"]]
                    expected["match_fields"] = ["waiting_pid", "blocking_pid"]
                elif expected["intent"] == "long_queries":
                    threshold = 60 if "60 saniye" in case["question"] else 25
                    expected["rows"] = [dict(pid=p, query_text=q) for p,q,duration in data["longs"]
                                        if duration > threshold]
                    expected["match_fields"] = ["pid", "query_text"]
                else:
                    metric = case.get("follow_up") or (
                        "mean" if any(x in case["question"] for x in ("ortalama", "çağrı başına"))
                        else "total")
                    column = "total_exec_time_ms" if metric == "total" else "mean_exec_time_ms"
                    count = 2 if "2 sorgu" in case["question"] else 1
                    expected["rows"] = sorted(data["stats"], key=lambda x: (-x[column], x["queryid"]))[:count]
                    # Captures continue during the run, so counter magnitudes move;
                    # the independently ranked query IDs are the stable outcome.
                    expected["match_fields"] = ["queryid"]
                item["expected"] = expected
            out.write(json.dumps(item, ensure_ascii=False) + "\n")
    (OUTPUT / "reference_batches.json").write_text(json.dumps(
        {key: val["batch_ids"] for key,val in by_condition.items()}, indent=2) + "\n", encoding="utf-8")
    print(f"Independent references: {len(cases)} cases -> {output}")


def smoke_case(config):
    """Create one real lock case before the 15-minute counter window matures."""
    case = next(case for case in load_cases(ROOT / "evaluation" / "controlled_cases.jsonl")
                if case["id"] == "D1-W1-01")
    with psycopg.connect(config["conditions"]["D1-W1"]["admin_url"], autocommit=True) as db:
        pairs = db.execute("""
            SELECT activity.pid, unnest(pg_blocking_pids(activity.pid))
            FROM pg_stat_activity activity WHERE activity.datname=current_database()
        """).fetchall()
    if not pairs:
        raise ValueError("D1-W1 has no live lock wait")
    case = {**case, "environment": "live", "expected": {
        "http": 200, "intent": "lock_waits", "ordered": False,
        "rows": [dict(waiting_pid=waiting, blocking_pid=blocking)
                 for waiting, blocking in pairs],
        "match_fields": ["waiting_pid", "blocking_pid"],
    }}
    path = OUTPUT / "small_cases.jsonl"
    path.write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Independent live lock reference: {pairs} -> {path}")


def live_stats_reference(config, case):
    """Rank raw snapshot deltas independently at the time of an API request."""
    condition = f"{case['dataset']}-{case['workload']}"
    entry = config["conditions"][condition]
    metric = case.get("follow_up") or (
        "mean" if any(term in case["question"] for term in ("ortalama", "çağrı başına"))
        else "total")
    count = 2 if "2 sorgu" in case["question"] else 1
    with psycopg.connect(entry["admin_url"], autocommit=True) as db:
        pair = db.execute("""
            WITH latest AS (
                SELECT batch_id, dbid, captured_at, stats_reset, dealloc
                FROM diagnostics.query_capture_batches
                WHERE dbid=(SELECT oid FROM pg_database WHERE datname=current_database())
                ORDER BY captured_at DESC, batch_id DESC LIMIT 1
            ), baseline AS (
                SELECT batch_id, dbid, captured_at, stats_reset, dealloc
                FROM diagnostics.query_capture_batches
                WHERE dbid=(SELECT dbid FROM latest)
                  AND captured_at <= (SELECT captured_at-interval '15 minutes' FROM latest)
                ORDER BY captured_at DESC, batch_id DESC LIMIT 1
            )
            SELECT latest.batch_id, baseline.batch_id,
                   extract(epoch FROM latest.captured_at-baseline.captured_at),
                   latest.stats_reset=baseline.stats_reset AND latest.dealloc=baseline.dealloc
            FROM latest CROSS JOIN baseline
        """).fetchone()
        if pair is None or not 900 <= pair[2] <= 1020 or not pair[3]:
            return {"batch_id": pair[0] if pair else None,
                    "queryids": [], "unavailable": True}
        rows = db.execute("""
            SELECT cur.queryid,
                   round(sum(cur.total_exec_time_ms-coalesce(old.total_exec_time_ms,0))::numeric,3)::float8,
                   round((sum(cur.total_exec_time_ms-coalesce(old.total_exec_time_ms,0)) /
                          sum(cur.calls-coalesce(old.calls,0)))::numeric,3)::float8,
                   sum(cur.calls-coalesce(old.calls,0))::bigint
            FROM diagnostics.query_counter_snapshots cur
            LEFT JOIN diagnostics.query_counter_snapshots old
              ON old.batch_id=%s AND old.userid=cur.userid AND old.dbid=cur.dbid
             AND old.queryid=cur.queryid AND old.toplevel=cur.toplevel
            WHERE cur.batch_id=%s AND cur.calls>coalesce(old.calls,0)
              AND cur.total_exec_time_ms>=coalesce(old.total_exec_time_ms,0)
            GROUP BY cur.queryid
        """, (pair[1], pair[0])).fetchall()
    column = 1 if metric == "total" else 2
    ranked = sorted(rows, key=lambda row: (-row[column], row[0]))[:count]
    return {"batch_id": pair[0], "queryids": [row[0] for row in ranked]}


def refresh_activity_cases(config):
    """Refresh volatile PIDs after restarting holder sessions, preserving stats cases."""
    path = OUTPUT / "live_cases.jsonl"
    cases = load_cases(path)
    activity = {}
    for condition, entry in config["conditions"].items():
        with psycopg.connect(entry["admin_url"], autocommit=True) as db:
            activity[condition] = {
                "locks": db.execute("""
                    SELECT a.pid, unnest(pg_blocking_pids(a.pid))
                    FROM pg_stat_activity a WHERE a.datname=current_database()
                """).fetchall(),
                "longs": db.execute("""
                    SELECT pid, left(query,500), extract(epoch FROM clock_timestamp()-query_start)
                    FROM pg_stat_activity
                    WHERE datname=current_database() AND state='active'
                      AND backend_type='client backend' AND pid<>pg_backend_pid()
                      AND query_start IS NOT NULL
                """).fetchall(),
            }
    with path.open("w", encoding="utf-8") as out:
        for case in cases:
            condition = f"{case['dataset']}-{case['workload']}"
            expected = case["expected"]
            if case["kind"] != "denied" and expected["intent"] == "lock_waits":
                expected["rows"] = [dict(waiting_pid=p, blocking_pid=b)
                                    for p,b in activity[condition]["locks"]]
            elif case["kind"] != "denied" and expected["intent"] == "long_queries":
                threshold = 60 if "60 saniye" in case["question"] else 25
                expected["rows"] = [dict(pid=p, query_text=q)
                                    for p,q,duration in activity[condition]["longs"]
                                    if duration > threshold]
            out.write(json.dumps(case, ensure_ascii=False) + "\n")
    print(f"Refreshed live activity references: {path}")


def serve(config):
    targets = {}
    children = []
    try:
        for index, (condition, entry) in enumerate(config["conditions"].items()):
            port = 8100 + index
            env = {**os.environ, "DIAGNOSTICS_AUTH_SECRET": config["auth_secret"],
                   "DIAGNOSTICS_DATABASE_URL": entry["operator_url"],
                   "DIAGNOSTICS_VIEWER_DATABASE_URL": entry["viewer_url"]}
            children.append(subprocess.Popen([
                sys.executable, "-m", "uvicorn", "api.app.main:app", "--host", "127.0.0.1",
                "--port", str(port)], cwd=ROOT, env=env))
            targets[condition] = f"http://127.0.0.1:{port}"
        (OUTPUT / "targets.json").write_text(json.dumps(targets, indent=2) + "\n", encoding="utf-8")
        print("Targets:", targets, flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for child in children:
            child.terminate()
        for child in children:
            child.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("setup", "rotate-credentials", "capture", "sample-loop", "workload", "workload-loop", "hold", "references", "refresh-activity", "smoke-case", "serve", "tokens"))
    parser.add_argument("--admin-url", default=os.getenv("TEST_POSTGRES_ADMIN_URL"))
    parser.add_argument("--repetitions", type=int, default=100)
    parser.add_argument("--interval-seconds", type=int, default=60)
    args = parser.parse_args()
    if args.action == "setup":
        if not args.admin_url:
            parser.error("Set TEST_POSTGRES_ADMIN_URL or --admin-url")
        setup(args.admin_url)
        return
    config = load()
    if args.action == "rotate-credentials":
        rotate_credentials(config)
    elif args.action == "capture":
        capture(config)
    elif args.action == "sample-loop":
        sample_loop(config, args.interval_seconds,
                    lambda current: capture(current, strict=False))
    elif args.action == "workload-loop":
        sample_loop(config, args.interval_seconds,
                    lambda current: workload(current, args.repetitions))
    elif args.action == "workload":
        workload(config, args.repetitions)
    elif args.action == "hold":
        hold(config)
    elif args.action == "references":
        references(config)
    elif args.action == "refresh-activity":
        refresh_activity_cases(config)
    elif args.action == "smoke-case":
        smoke_case(config)
    elif args.action == "serve":
        serve(config)
    else:
        for role in ("operator", "viewer"):
            print(f"$env:DIAGNOSTICS_{role.upper()}_JWT = '{signed_test_token(role, config['auth_secret'], 240)}'")


if __name__ == "__main__":
    main()
