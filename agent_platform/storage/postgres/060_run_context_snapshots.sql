-- Immutable Run context snapshots used to rebuild execution after worker recovery.
BEGIN;

CREATE TABLE IF NOT EXISTS platform_run_context_snapshots (
    tenant_id          text NOT NULL,
    run_id             uuid NOT NULL,
    revision           integer NOT NULL CHECK (revision > 0),
    snapshot_id        uuid NOT NULL UNIQUE,
    context_payload    jsonb NOT NULL,
    references_payload jsonb NOT NULL DEFAULT '[]'::jsonb,
    digest             text NOT NULL CHECK (length(digest) = 64 AND digest ~ '^[0-9a-f]{64}
    created_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, run_id, revision),
    FOREIGN KEY (tenant_id, run_id)
        REFERENCES platform_runs(tenant_id, run_id) ON DELETE CASCADE,
    CHECK (jsonb_typeof(context_payload) = 'object'),
    CHECK (jsonb_typeof(references_payload) = 'array')
);

CREATE INDEX IF NOT EXISTS idx_run_context_snapshots_latest
    ON platform_run_context_snapshots (tenant_id, run_id, revision DESC);

COMMIT;
),
    created_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, run_id, revision),
    FOREIGN KEY (tenant_id, run_id)
        REFERENCES platform_runs(tenant_id, run_id) ON DELETE CASCADE,
    CHECK (jsonb_typeof(context_payload) = 'object'),
    CHECK (jsonb_typeof(references_payload) = 'array')
);

CREATE INDEX IF NOT EXISTS idx_run_context_snapshots_latest
    ON platform_run_context_snapshots (tenant_id, run_id, revision DESC);

COMMIT;
