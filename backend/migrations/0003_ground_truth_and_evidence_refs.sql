-- 1. Ground truth moves out of scenarios into a table that NO application role can read.
--    Only the table owner (admin: seed + offline evaluation) has access.
CREATE TABLE scenario_ground_truth (
    scenario_id           bigint PRIMARY KEY REFERENCES scenarios (id) ON DELETE CASCADE,
    split                 text NOT NULL CHECK (split IN ('dev', 'holdout')),
    expected_root_cause   text NOT NULL,
    root_cause_service_id bigint NOT NULL REFERENCES services (id) ON DELETE RESTRICT,
    -- Keyword groups for scoring: every group must match at least one of its keywords.
    root_cause_keywords   jsonb NOT NULL DEFAULT '[]',
    key_evidence_ids      text[] NOT NULL DEFAULT '{}'
);
REVOKE ALL ON scenario_ground_truth FROM PUBLIC;

INSERT INTO scenario_ground_truth (scenario_id, split, expected_root_cause, root_cause_service_id)
SELECT id, 'dev', expected_root_cause, root_cause_service_id
FROM scenarios
WHERE root_cause_service_id IS NOT NULL;

REVOKE SELECT (id, slug, title, alert_summary, incident_start, created_at) ON scenarios FROM tools_ro;
ALTER TABLE scenarios DROP COLUMN expected_root_cause, DROP COLUMN root_cause_service_id;
-- With the ground truth gone, scenarios is safe to read in full.
GRANT SELECT ON scenarios TO tools_ro;

-- 2. Stable, scenario-local evidence IDs (e.g. 'log:0153', 'deploy:2') for citations.
ALTER TABLE logs ADD COLUMN ref text NOT NULL CHECK (ref ~ '^log:[0-9]{4}$');
ALTER TABLE logs ADD CONSTRAINT logs_scenario_ref_key UNIQUE (scenario_id, ref);
ALTER TABLE deployments ADD COLUMN ref text NOT NULL CHECK (ref ~ '^deploy:[0-9]+$');
ALTER TABLE deployments ADD CONSTRAINT deployments_scenario_ref_key UNIQUE (scenario_id, ref);
