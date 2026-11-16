BEGIN;

CREATE TABLE IF NOT EXISTS platform_run_context_snapshots (
    tenant_id       text NOT NULL,
    run_id          uuid NOT NULL,
    revision        integer NOT NULL CHECK (revision > 0),
    snapshot_id     uuid NOT NULL,
    context         jsonb NOT NULL,
    references      jsonb NOT NULL DEFAULT '[]'::jsonb,
    digest          text NOT NULL CHECK (length(digest) = 64),
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, run_id, revision),
    UNIQUE (tenant_id, snapshot_id),
    FOREIGN KEY (tenant_id, run_id)
        REFERENCES platform_runs (tenant_id, run_id) ON DELETE CASCADE,
    CHECK (jsonb_typeof(context) = 'object'),
    CHECK (jsonb_typeof(references) = 'array')
);

CREATE INDEX IF NOT EXISTS idx_platform_run_context_latest
    ON platform_run_context_snapshots (tenant_id, run_id, revision DESC);

COMMIT;
