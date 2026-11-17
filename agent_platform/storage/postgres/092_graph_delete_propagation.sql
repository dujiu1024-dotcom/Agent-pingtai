-- Durable delete tombstones prevent stale concurrent graph upserts from resurrecting
-- deleted entities. The outbox carries cache/index invalidations across process restarts.
BEGIN;

CREATE TABLE IF NOT EXISTS graph_entity_tombstones (
    tenant_id text NOT NULL,
    entity_id text NOT NULL,
    deleted_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, entity_id)
);

CREATE TABLE IF NOT EXISTS graph_invalidation_outbox (
    job_id bigserial PRIMARY KEY,
    tenant_id text NOT NULL,
    object_kind text NOT NULL CHECK (object_kind IN ('entity', 'relation', 'cache')),
    object_id text NOT NULL,
    operation text NOT NULL CHECK (operation IN ('delete', 'invalidate')),
    idempotency_key text NOT NULL,
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'completed', 'dead')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at timestamptz NOT NULL DEFAULT now(),
    lease_owner text,
    lease_until timestamptz,
    completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, idempotency_key),
    CHECK ((status = 'processing' AND lease_owner IS NOT NULL AND lease_until IS NOT NULL)
        OR (status <> 'processing' AND lease_owner IS NULL AND lease_until IS NULL))
);

CREATE INDEX IF NOT EXISTS graph_invalidation_outbox_claim_idx
    ON graph_invalidation_outbox (tenant_id, status, available_at, job_id);

COMMIT;
