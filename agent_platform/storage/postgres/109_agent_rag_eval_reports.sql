-- Immutable, tenant-scoped Agent/RAG evaluation runner reports.
BEGIN;

CREATE TABLE IF NOT EXISTS platform_agent_rag_eval_reports (
    run_id uuid NOT NULL,
    tenant_id text NOT NULL REFERENCES platform_tenants(tenant_id) ON DELETE CASCADE,
    dataset_id text NOT NULL,
    dataset_version integer NOT NULL CHECK (dataset_version > 0),
    dataset_sha256 text NOT NULL CHECK (dataset_sha256 ~ '^[0-9a-f]{64}$'),
    endpoint_id text NOT NULL,
    model_key text NOT NULL,
    model_version text NOT NULL,
    concurrency_limit integer NOT NULL CHECK (concurrency_limit BETWEEN 1 AND 32),
    report jsonb NOT NULL CHECK (jsonb_typeof(report) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, run_id),
    CHECK (length(dataset_id) > 0),
    CHECK (length(endpoint_id) > 0),
    CHECK (length(model_key) > 0),
    CHECK (length(model_version) > 0)
);

CREATE INDEX IF NOT EXISTS idx_agent_rag_eval_reports_tenant_recent
    ON platform_agent_rag_eval_reports(tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_agent_rag_eval_reports_tenant_dataset_recent
    ON platform_agent_rag_eval_reports(tenant_id, dataset_id, dataset_version, created_at DESC);

CREATE OR REPLACE FUNCTION platform_reject_agent_rag_eval_report_mutation()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'Agent/RAG evaluation reports are immutable'
      USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS trg_agent_rag_eval_reports_immutable
    ON platform_agent_rag_eval_reports;
CREATE TRIGGER trg_agent_rag_eval_reports_immutable
    BEFORE UPDATE OR DELETE ON platform_agent_rag_eval_reports
    FOR EACH ROW EXECUTE FUNCTION platform_reject_agent_rag_eval_report_mutation();

REVOKE UPDATE, DELETE, TRUNCATE ON platform_agent_rag_eval_reports FROM PUBLIC;

COMMIT;
