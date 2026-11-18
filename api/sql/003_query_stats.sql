-- PostgreSQL 17. Start the server with shared_preload_libraries=pg_stat_statements.
-- Run this file as the trusted owner in every target database.
CREATE EXTENSION IF NOT EXISTS pg_stat_statements WITH SCHEMA public;
CREATE SCHEMA IF NOT EXISTS diagnostics;

CREATE TABLE IF NOT EXISTS diagnostics.query_capture_batches (
    batch_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dbid oid NOT NULL,
    captured_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    stats_reset timestamptz NOT NULL,
    dealloc bigint NOT NULL
);

CREATE INDEX IF NOT EXISTS query_capture_batches_lookup
    ON diagnostics.query_capture_batches (dbid, captured_at DESC, batch_id DESC);

CREATE TABLE IF NOT EXISTS diagnostics.query_counter_snapshots (
    batch_id bigint NOT NULL REFERENCES diagnostics.query_capture_batches(batch_id) ON DELETE CASCADE,
    userid oid NOT NULL,
    dbid oid NOT NULL,
    queryid bigint NOT NULL,
    toplevel boolean NOT NULL,
    calls bigint NOT NULL,
    total_exec_time_ms double precision NOT NULL,
    PRIMARY KEY (batch_id, userid, dbid, queryid, toplevel)
);

CREATE OR REPLACE FUNCTION diagnostics.capture_query_stats()
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, diagnostics
AS $$
DECLARE
    captured_batch bigint;
    current_dbid oid;
BEGIN
    SELECT oid INTO STRICT current_dbid
    FROM pg_catalog.pg_database WHERE datname = current_database();

    INSERT INTO diagnostics.query_capture_batches (dbid, stats_reset, dealloc)
    SELECT current_dbid, info.stats_reset, info.dealloc
    FROM public.pg_stat_statements_info AS info
    RETURNING batch_id INTO STRICT captured_batch;

    INSERT INTO diagnostics.query_counter_snapshots
        (batch_id, userid, dbid, queryid, toplevel, calls, total_exec_time_ms)
    SELECT captured_batch, stats.userid, stats.dbid, stats.queryid,
           stats.toplevel, stats.calls, stats.total_exec_time
    FROM public.pg_stat_statements AS stats
    WHERE stats.dbid = current_dbid AND stats.queryid IS NOT NULL;

    RETURN captured_batch;
END;
$$;

REVOKE ALL ON FUNCTION diagnostics.capture_query_stats() FROM PUBLIC;

CREATE OR REPLACE VIEW diagnostics.query_stats_health AS
WITH current_db AS (
    SELECT oid AS dbid FROM pg_catalog.pg_database WHERE datname = current_database()
), latest AS (
    SELECT batch.* FROM diagnostics.query_capture_batches AS batch
    JOIN current_db USING (dbid)
    ORDER BY batch.captured_at DESC, batch.batch_id DESC LIMIT 1
), baseline AS (
    SELECT batch.* FROM diagnostics.query_capture_batches AS batch
    JOIN latest ON batch.dbid = latest.dbid
    WHERE batch.captured_at <= latest.captured_at - interval '15 minutes'
    ORDER BY batch.captured_at DESC, batch.batch_id DESC LIMIT 1
)
SELECT COALESCE(
    latest.captured_at >= clock_timestamp() - interval '2 minutes'
    AND baseline.captured_at >= latest.captured_at - interval '17 minutes'
    AND latest.stats_reset = baseline.stats_reset
    AND latest.dealloc = baseline.dealloc
    AND NOT EXISTS (
        SELECT 1
        FROM diagnostics.query_counter_snapshots AS current_row
        JOIN diagnostics.query_counter_snapshots AS old_row
          ON current_row.userid = old_row.userid
         AND current_row.dbid = old_row.dbid
         AND current_row.queryid = old_row.queryid
         AND current_row.toplevel = old_row.toplevel
        WHERE current_row.batch_id = latest.batch_id
          AND old_row.batch_id = baseline.batch_id
          AND (current_row.calls < old_row.calls
               OR current_row.total_exec_time_ms < old_row.total_exec_time_ms)
    ), false
) AS sufficient
FROM (SELECT 1) AS anchor
LEFT JOIN latest ON true
LEFT JOIN baseline ON true;

CREATE OR REPLACE VIEW diagnostics.query_stats AS
WITH current_db AS (
    SELECT oid AS dbid FROM pg_catalog.pg_database WHERE datname = current_database()
), latest AS (
    SELECT batch.* FROM diagnostics.query_capture_batches AS batch
    JOIN current_db USING (dbid)
    ORDER BY batch.captured_at DESC, batch.batch_id DESC LIMIT 1
), baseline AS (
    SELECT batch.* FROM diagnostics.query_capture_batches AS batch
    JOIN latest ON batch.dbid = latest.dbid
    WHERE batch.captured_at <= latest.captured_at - interval '15 minutes'
    ORDER BY batch.captured_at DESC, batch.batch_id DESC LIMIT 1
), deltas AS (
    SELECT current_row.queryid,
           current_row.calls - COALESCE(old_row.calls, 0) AS calls_delta,
           current_row.total_exec_time_ms - COALESCE(old_row.total_exec_time_ms, 0) AS time_delta
    FROM latest
    JOIN baseline ON true
    JOIN diagnostics.query_counter_snapshots AS current_row
      ON current_row.batch_id = latest.batch_id
    LEFT JOIN diagnostics.query_counter_snapshots AS old_row
      ON old_row.batch_id = baseline.batch_id
     AND old_row.userid = current_row.userid
     AND old_row.dbid = current_row.dbid
     AND old_row.queryid = current_row.queryid
     AND old_row.toplevel = current_row.toplevel
    WHERE current_row.calls >= COALESCE(old_row.calls, 0)
      AND current_row.total_exec_time_ms >= COALESCE(old_row.total_exec_time_ms, 0)
)
SELECT deltas.queryid,
       round(sum(deltas.time_delta)::numeric, 3)::double precision AS total_exec_time_ms,
       round((sum(deltas.time_delta) / sum(deltas.calls_delta))::numeric, 3)::double precision
           AS mean_exec_time_ms,
       sum(deltas.calls_delta)::bigint AS calls
FROM deltas
CROSS JOIN diagnostics.query_stats_health AS health
WHERE health.sufficient AND deltas.calls_delta > 0
GROUP BY deltas.queryid;
