BEGIN;
CREATE TABLE IF NOT EXISTS platform_event_receipts (
    tenant_id text NOT NULL REFERENCES platform_tenants(tenant_id),
    consumer_name text NOT NULL,
    event_id uuid NOT NULL,
    status text NOT NULL CHECK (status IN ('processing', 'completed')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    lease_token uuid,
    lease_until timestamptz,
    last_error text,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    PRIMARY KEY (tenant_id, consumer_name, event_id),
    CHECK (status <> 'completed' OR (completed_at IS NOT NULL AND lease_token IS NULL AND lease_until IS NULL))
);
CREATE INDEX IF NOT EXISTS idx_platform_event_receipts_processing
    ON platform_event_receipts (lease_until)
    WHERE status = 'processing';
COMMIT;
