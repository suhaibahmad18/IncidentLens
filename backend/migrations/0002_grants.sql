-- Least-privilege access for the two login roles created by app.db.bootstrap.
-- Any migration that adds a table must add its grants too (the privilege-matrix test enforces this).

-- Only the two roles may connect; PUBLIC also loses TEMPORARY on this database.
DO $$
BEGIN
    EXECUTE format('REVOKE ALL ON DATABASE %I FROM PUBLIC', current_database());
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO incidentlens_app, tools_ro', current_database());
END
$$;

REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO incidentlens_app, tools_ro;

-- Input data: readable by both roles; nobody but the owner can write it.
GRANT SELECT ON services, logs, metrics, deployments, doc_chunks TO incidentlens_app, tools_ro;
GRANT SELECT ON scenarios TO incidentlens_app;
-- tools_ro never sees the ground truth (expected_root_cause, root_cause_service_id).
GRANT SELECT (id, slug, title, alert_summary, incident_start, created_at) ON scenarios TO tools_ro;

-- Output data: written by the application only. tools_ro gets nothing here.
GRANT SELECT, INSERT, UPDATE ON runs, eval_runs, eval_results TO incidentlens_app;
GRANT SELECT, INSERT ON trace_steps TO incidentlens_app;  -- append-only audit trail
GRANT SELECT, INSERT, UPDATE, DELETE ON llm_cache TO incidentlens_app;
