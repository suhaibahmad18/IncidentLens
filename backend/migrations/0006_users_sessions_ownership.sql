-- Accounts, server-side sessions, and ownership of user-created incidents.

-- 1. Accounts. Emails are stored lower-cased; only an Argon2id hash of the password is stored.
CREATE TABLE users (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email         text NOT NULL UNIQUE CHECK (email = lower(email) AND length(email) BETWEEN 3 AND 254),
    password_hash text NOT NULL CHECK (password_hash LIKE '$argon2id$%'),
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- 2. Sessions. The cookie carries a random token; only its SHA-256 hash is stored here, so a
--    database leak does not reveal usable session tokens. Logout deletes the row.
CREATE TABLE sessions (
    token_hash text PRIMARY KEY CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    user_id    bigint NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    csrf_token text NOT NULL CHECK (length(csrf_token) >= 32),
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL CHECK (expires_at > created_at)
);
CREATE INDEX sessions_user_idx ON sessions (user_id);
CREATE INDEX sessions_expires_idx ON sessions (expires_at);

-- 3. Every user-created incident belongs to exactly one account; demos belong to nobody.
--    NOT VALID: incidents created before accounts existed keep owner_id NULL (they are visible
--    to nobody until an admin assigns them or they are pruned); every NEW row is checked.
ALTER TABLE scenarios ADD COLUMN owner_id bigint REFERENCES users (id) ON DELETE CASCADE;
CREATE INDEX scenarios_owner_idx ON scenarios (owner_id) WHERE owner_id IS NOT NULL;
ALTER TABLE scenarios ADD CONSTRAINT scenarios_owner_matches_kind
    CHECK ((kind = 'demo') = (owner_id IS NULL)) NOT VALID;

-- 4. Privileges. tools_ro (the agent) gets nothing on accounts or sessions.
GRANT SELECT, INSERT ON users TO incidentlens_app;
GRANT SELECT, INSERT, DELETE ON sessions TO incidentlens_app;
GRANT INSERT (owner_id) ON scenarios TO incidentlens_app;
-- Owners may delete their own incidents (the API checks ownership); evidence and trace steps
-- follow through foreign-key cascades. Demos are protected by the triggers below.
GRANT DELETE ON scenarios, runs TO incidentlens_app;

CREATE FUNCTION reject_deleting_demo_data() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    demo boolean;
BEGIN
    IF current_user = (SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = TG_RELID) THEN
        RETURN OLD;  -- the table owner (admin: seed, prune) is unrestricted
    END IF;
    IF TG_TABLE_NAME = 'scenarios' THEN
        demo := OLD.kind = 'demo';
    ELSE
        demo := EXISTS (SELECT 1 FROM scenarios WHERE id = OLD.scenario_id AND kind = 'demo');
    END IF;
    IF demo THEN
        RAISE EXCEPTION 'demo incidents are read-only' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN OLD;
END
$$;

CREATE TRIGGER scenarios_protect_demo_delete BEFORE DELETE ON scenarios
    FOR EACH ROW EXECUTE FUNCTION reject_deleting_demo_data();
CREATE TRIGGER runs_protect_demo_delete BEFORE DELETE ON runs
    FOR EACH ROW EXECUTE FUNCTION reject_deleting_demo_data();
