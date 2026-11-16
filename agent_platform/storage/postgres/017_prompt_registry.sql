BEGIN;

CREATE TABLE IF NOT EXISTS platform_prompt_versions (
    prompt_id text NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    template text NOT NULL,
    variables text[] NOT NULL,
    content_hash char(64) NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (prompt_id, version)
);

CREATE TABLE IF NOT EXISTS platform_prompt_deployments (
    prompt_id text PRIMARY KEY,
    active_version integer NOT NULL,
    canary_version integer,
    canary_percentage numeric(5,2) NOT NULL DEFAULT 0
      CHECK (canary_percentage >= 0 AND canary_percentage <= 100),
    updated_by text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (prompt_id, active_version)
      REFERENCES platform_prompt_versions(prompt_id, version),
    FOREIGN KEY (prompt_id, canary_version)
      REFERENCES platform_prompt_versions(prompt_id, version),
    CHECK (canary_version IS NULL OR canary_version <> active_version)
);

CREATE OR REPLACE FUNCTION platform_reject_prompt_version_mutation()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'prompt versions are immutable'
      USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS trg_prompt_versions_immutable ON platform_prompt_versions;
CREATE TRIGGER trg_prompt_versions_immutable
  BEFORE UPDATE OR DELETE ON platform_prompt_versions
  FOR EACH ROW EXECUTE FUNCTION platform_reject_prompt_version_mutation();

REVOKE UPDATE, DELETE, TRUNCATE ON platform_prompt_versions FROM PUBLIC;

COMMIT;
