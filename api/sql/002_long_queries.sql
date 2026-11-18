-- Run as the trusted diagnostics view owner in each target database.
CREATE SCHEMA IF NOT EXISTS diagnostics;

CREATE OR REPLACE FUNCTION diagnostics.current_long_queries()
RETURNS TABLE (pid integer, duration_seconds double precision, query_text text)
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, diagnostics
AS $$
SELECT
    activity.pid::integer AS pid,
    GREATEST(
        0,
        EXTRACT(EPOCH FROM clock_timestamp() - activity.query_start)
    )::double precision AS duration_seconds,
    left(activity.query, 500)::text AS query_text
FROM pg_catalog.pg_stat_activity AS activity
WHERE activity.datname = current_database()
  AND activity.state = 'active'
  AND activity.backend_type = 'client backend'
  AND activity.query_start IS NOT NULL
  AND activity.pid <> pg_backend_pid();
$$;

REVOKE ALL ON FUNCTION diagnostics.current_long_queries() FROM PUBLIC;

CREATE OR REPLACE VIEW diagnostics.long_queries AS
SELECT pid, duration_seconds, query_text
FROM diagnostics.current_long_queries();
