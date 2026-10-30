-- When each evidence record was stored, so the API can tell whether a report predates the
-- latest evidence (and retention can reason about age). Existing rows get the migration time.
ALTER TABLE logs ADD COLUMN created_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE metrics ADD COLUMN created_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE deployments ADD COLUMN created_at timestamptz NOT NULL DEFAULT now();
