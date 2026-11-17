BEGIN;

CREATE TABLE IF NOT EXISTS platform_prompt_audit_events (
    event_id bigserial PRIMARY KEY,
    prompt_id text NOT NULL,
    action text NOT NULL CHECK (action IN (
        'version.registered', 'version.activated', 'version.rolled_back',
        'canary.configured'
    )),
    actor_id text NOT NULL,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_prompt_audit_prompt_event
    ON platform_prompt_audit_events (prompt_id, event_id DESC);

CREATE OR REPLACE FUNCTION platform_reject_prompt_audit_mutation()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'prompt audit events are append-only'
      USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS trg_prompt_audit_events_immutable ON platform_prompt_audit_events;
CREATE TRIGGER trg_prompt_audit_events_immutable
  BEFORE UPDATE OR DELETE ON platform_prompt_audit_events
  FOR EACH ROW EXECUTE FUNCTION platform_reject_prompt_audit_mutation();

REVOKE UPDATE, DELETE, TRUNCATE ON platform_prompt_audit_events FROM PUBLIC;

COMMIT;
