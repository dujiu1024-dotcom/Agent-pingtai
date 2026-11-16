-- Persist immutable, source-attributed model pricing snapshots for cost accounting.
BEGIN;

CREATE TABLE IF NOT EXISTS platform_model_pricing_cards (
    model_key text NOT NULL,
    version text NOT NULL,
    input_usd_per_million numeric(24, 12) NOT NULL CHECK (input_usd_per_million >= 0),
    output_usd_per_million numeric(24, 12) NOT NULL CHECK (output_usd_per_million >= 0),
    currency text NOT NULL DEFAULT 'USD' CHECK (currency = 'USD'),
    effective_at timestamptz NOT NULL,
    source text NOT NULL,
    source_revision text NOT NULL,
    content_hash text NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (model_key, version),
    UNIQUE (source, source_revision, model_key)
);

CREATE INDEX IF NOT EXISTS idx_model_pricing_cards_effective
    ON platform_model_pricing_cards(model_key, effective_at DESC, created_at DESC);

CREATE OR REPLACE FUNCTION platform_reject_model_pricing_mutation()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'model pricing cards are immutable'
      USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS trg_model_pricing_cards_immutable ON platform_model_pricing_cards;
CREATE TRIGGER trg_model_pricing_cards_immutable
  BEFORE UPDATE OR DELETE ON platform_model_pricing_cards
  FOR EACH ROW EXECUTE FUNCTION platform_reject_model_pricing_mutation();

REVOKE UPDATE, DELETE, TRUNCATE ON platform_model_pricing_cards FROM PUBLIC;

COMMIT;
