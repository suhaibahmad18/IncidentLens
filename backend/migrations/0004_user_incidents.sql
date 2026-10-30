-- User-created incidents alongside the seeded demos.

-- 1. Demo vs user. Existing rows are demos; the default for new rows becomes 'user', and the
--    application role cannot set the column (it has no INSERT privilege on it, see below).
ALTER TABLE scenarios ADD COLUMN kind text NOT NULL DEFAULT 'demo' CHECK (kind IN ('demo', 'user'));
ALTER TABLE scenarios ALTER COLUMN kind SET DEFAULT 'user';

-- 2. The application may CREATE user incidents and their evidence. INSERT only: there is still
--    no UPDATE or DELETE on any input table, and tools_ro stays read-only.
GRANT INSERT (slug, title, alert_summary, incident_start) ON scenarios TO incidentlens_app;
GRANT INSERT (name) ON services TO incidentlens_app;
GRANT INSERT ON logs, metrics, deployments TO incidentlens_app;

-- 3. Demo incidents are read-only for everyone except the table owner (the admin seed).
CREATE FUNCTION reject_writes_to_demo_incidents() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF current_user <> (SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = TG_RELID)
       AND EXISTS (SELECT 1 FROM scenarios WHERE id = NEW.scenario_id AND kind = 'demo') THEN
        RAISE EXCEPTION 'demo incidents are read-only' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER logs_protect_demo BEFORE INSERT OR UPDATE ON logs
    FOR EACH ROW EXECUTE FUNCTION reject_writes_to_demo_incidents();
CREATE TRIGGER metrics_protect_demo BEFORE INSERT OR UPDATE ON metrics
    FOR EACH ROW EXECUTE FUNCTION reject_writes_to_demo_incidents();
CREATE TRIGGER deployments_protect_demo BEFORE INSERT OR UPDATE ON deployments
    FOR EACH ROW EXECUTE FUNCTION reject_writes_to_demo_incidents();
