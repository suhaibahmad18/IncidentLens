-- IncidentLens initial schema.
-- Input data (what the agent investigates): services, scenarios, logs, metrics, deployments, doc_chunks.
-- Output data (what the agent and evaluator produce): runs, trace_steps, eval_runs, eval_results, llm_cache.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE services (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        text NOT NULL UNIQUE CHECK (name <> ''),
    description text NOT NULL DEFAULT '',
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE scenarios (
    id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    slug                  text NOT NULL UNIQUE CHECK (slug <> ''),
    title                 text NOT NULL,
    alert_summary         text NOT NULL,
    incident_start        timestamptz NOT NULL,
    -- Ground truth for evaluation. Hidden from tools_ro by column-level grants (0002).
    expected_root_cause   text NOT NULL,
    root_cause_service_id bigint REFERENCES services (id) ON DELETE RESTRICT,
    created_at            timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE logs (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    scenario_id bigint NOT NULL REFERENCES scenarios (id) ON DELETE CASCADE,
    service_id  bigint NOT NULL REFERENCES services (id) ON DELETE RESTRICT,
    ts          timestamptz NOT NULL,
    level       text NOT NULL CHECK (level IN ('DEBUG', 'INFO', 'WARN', 'ERROR', 'FATAL')),
    message     text NOT NULL,
    attributes  jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX logs_scenario_service_ts_idx ON logs (scenario_id, service_id, ts);
CREATE INDEX logs_scenario_level_ts_idx ON logs (scenario_id, level, ts);

CREATE TABLE metrics (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    scenario_id bigint NOT NULL REFERENCES scenarios (id) ON DELETE CASCADE,
    service_id  bigint NOT NULL REFERENCES services (id) ON DELETE RESTRICT,
    name        text NOT NULL CHECK (name <> ''),
    ts          timestamptz NOT NULL,
    value       double precision NOT NULL,
    unit        text NOT NULL DEFAULT '',
    UNIQUE (scenario_id, service_id, name, ts)
);

CREATE TABLE deployments (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    scenario_id    bigint NOT NULL REFERENCES scenarios (id) ON DELETE CASCADE,
    service_id     bigint NOT NULL REFERENCES services (id) ON DELETE RESTRICT,
    version        text NOT NULL,
    deployed_at    timestamptz NOT NULL,
    status         text NOT NULL CHECK (status IN ('succeeded', 'failed', 'rolled_back')),
    change_summary text NOT NULL DEFAULT '',
    deployed_by    text NOT NULL DEFAULT ''
);
CREATE INDEX deployments_scenario_service_ts_idx ON deployments (scenario_id, service_id, deployed_at);

-- Runbook chunks: shared knowledge, intentionally not tied to a scenario.
-- vector(384) matches BAAI/bge-small-en-v1.5 (FastEmbed). Changing models means changing this.
CREATE TABLE doc_chunks (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source      text NOT NULL CHECK (source <> ''),
    chunk_index integer NOT NULL CHECK (chunk_index >= 0),
    title       text NOT NULL DEFAULT '',
    content     text NOT NULL CHECK (content <> ''),
    embedding   vector(384),
    metadata    jsonb NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source, chunk_index)
);

CREATE TABLE runs (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scenario_id  bigint NOT NULL REFERENCES scenarios (id) ON DELETE RESTRICT,
    status       text NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    model        text NOT NULL,
    final_report jsonb,
    error        text,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz CHECK (finished_at IS NULL OR finished_at >= started_at)
);
CREATE INDEX runs_scenario_started_idx ON runs (scenario_id, started_at DESC);

CREATE TABLE trace_steps (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id        uuid NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    step_index    integer NOT NULL CHECK (step_index >= 0),
    step_type     text NOT NULL CHECK (step_type IN ('llm_call', 'tool_call', 'retrieval', 'final')),
    name          text NOT NULL DEFAULT '',
    input         jsonb NOT NULL DEFAULT '{}',
    output        jsonb,
    error         text,
    latency_ms    integer CHECK (latency_ms >= 0),
    input_tokens  integer CHECK (input_tokens >= 0),
    output_tokens integer CHECK (output_tokens >= 0),
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, step_index)
);

CREATE TABLE eval_runs (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    status      text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'completed', 'failed')),
    model       text NOT NULL,
    config      jsonb NOT NULL DEFAULT '{}',
    git_commit  text,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz CHECK (finished_at IS NULL OR finished_at >= started_at)
);

CREATE TABLE eval_results (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    eval_run_id        uuid NOT NULL REFERENCES eval_runs (id) ON DELETE CASCADE,
    scenario_id        bigint NOT NULL REFERENCES scenarios (id) ON DELETE RESTRICT,
    run_id             uuid REFERENCES runs (id) ON DELETE SET NULL,
    root_cause_correct boolean NOT NULL,
    score              double precision NOT NULL CHECK (score BETWEEN 0 AND 1),
    details            jsonb NOT NULL DEFAULT '{}',
    created_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (eval_run_id, scenario_id)
);

CREATE TABLE llm_cache (
    cache_key  text PRIMARY KEY CHECK (cache_key ~ '^[0-9a-f]{64}$'),
    model      text NOT NULL,
    request    jsonb NOT NULL,
    response   jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
