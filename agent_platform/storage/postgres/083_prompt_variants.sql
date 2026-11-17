BEGIN;

CREATE TABLE IF NOT EXISTS platform_prompt_variants (
    prompt_id text NOT NULL,
    variant_name text NOT NULL CHECK (variant_name ~ '^[a-z][a-z0-9_-]{0,63}$'),
    current_version integer NOT NULL CHECK (current_version > 0),
    status text NOT NULL DEFAULT 'active'
      CHECK (status IN ('active', 'deprecated')),
    created_by text NOT NULL,
    updated_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (prompt_id, variant_name),
    FOREIGN KEY (prompt_id, current_version)
      REFERENCES platform_prompt_versions(prompt_id, version)
);

CREATE INDEX IF NOT EXISTS idx_prompt_variants_status
    ON platform_prompt_variants (prompt_id, status, variant_name);

CREATE TABLE IF NOT EXISTS platform_prompt_variant_versions (
    event_id bigserial PRIMARY KEY,
    prompt_id text NOT NULL,
    variant_name text NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    changed_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (prompt_id, variant_name)
      REFERENCES platform_prompt_variants(prompt_id, variant_name),
    FOREIGN KEY (prompt_id, version)
      REFERENCES platform_prompt_versions(prompt_id, version)
);

CREATE INDEX IF NOT EXISTS idx_prompt_variant_versions_history
    ON platform_prompt_variant_versions (prompt_id, variant_name, created_at, event_id);

ALTER TABLE platform_prompt_audit_events
    DROP CONSTRAINT IF EXISTS platform_prompt_audit_events_action_check;

ALTER TABLE platform_prompt_audit_events
    ADD CONSTRAINT platform_prompt_audit_events_action_check CHECK (action IN (
        'version.registered', 'version.activated', 'version.rolled_back',
        'canary.configured', 'variant.created', 'variant.version_assigned',
        'variant.deprecated'
    ));

COMMIT;
