-- Temporary contract until Yusuf's PostgreSQL view is available.
-- Run as a trusted view owner. Grant only schema usage and SELECT on this view
-- to the API's restricted database role.
CREATE SCHEMA IF NOT EXISTS diagnostics;

CREATE OR REPLACE VIEW diagnostics.lock_waits AS
SELECT
    waiter.pid::integer AS waiting_pid,
    blocker.pid::integer AS blocking_pid,
    GREATEST(
        0,
        EXTRACT(EPOCH FROM clock_timestamp() - COALESCE(wait_lock.waitstart, clock_timestamp()))::integer
    ) AS wait_seconds
FROM pg_catalog.pg_stat_activity AS waiter
CROSS JOIN LATERAL unnest(pg_catalog.pg_blocking_pids(waiter.pid)) AS blocker(pid)
LEFT JOIN LATERAL (
    SELECT min(lock_row.waitstart) AS waitstart
    FROM pg_catalog.pg_locks AS lock_row
    WHERE lock_row.pid = waiter.pid AND NOT lock_row.granted
) AS wait_lock ON TRUE;
