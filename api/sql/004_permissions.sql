-- Run as a trusted administrator after creating the three views.
-- Login roles receive membership in one of these NOLOGIN roles.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'diagnostics_operator_reader') THEN
        CREATE ROLE diagnostics_operator_reader NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'diagnostics_viewer_reader') THEN
        CREATE ROLE diagnostics_viewer_reader NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'diagnostics_sampler') THEN
        CREATE ROLE diagnostics_sampler NOLOGIN;
    END IF;
END;
$$;

REVOKE ALL ON SCHEMA diagnostics FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA diagnostics FROM PUBLIC;
REVOKE ALL ON FUNCTION diagnostics.capture_query_stats() FROM PUBLIC;
REVOKE ALL ON FUNCTION diagnostics.current_long_queries() FROM PUBLIC;
REVOKE ALL ON public.pg_stat_statements, public.pg_stat_statements_info FROM PUBLIC;
GRANT USAGE ON SCHEMA diagnostics TO diagnostics_operator_reader, diagnostics_viewer_reader;
GRANT USAGE ON SCHEMA diagnostics TO diagnostics_sampler;
GRANT EXECUTE ON FUNCTION diagnostics.capture_query_stats() TO diagnostics_sampler;
GRANT EXECUTE ON FUNCTION diagnostics.current_long_queries() TO diagnostics_operator_reader;
GRANT SELECT ON diagnostics.lock_waits, diagnostics.long_queries,
                diagnostics.query_stats, diagnostics.query_stats_health
    TO diagnostics_operator_reader;
GRANT SELECT ON diagnostics.query_stats, diagnostics.query_stats_health
    TO diagnostics_viewer_reader;
REVOKE ALL ON diagnostics.query_capture_batches, diagnostics.query_counter_snapshots
    FROM diagnostics_operator_reader, diagnostics_viewer_reader;
