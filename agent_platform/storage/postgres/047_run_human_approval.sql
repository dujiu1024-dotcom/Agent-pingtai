BEGIN;

-- Human approval is a first-class intervention kind. Decisions are recorded
-- against the immutable request row and mirrored to the Run event timeline.
ALTER TABLE platform_run_interventions
    DROP CONSTRAINT IF EXISTS platform_run_interventions_kind_check;
ALTER TABLE platform_run_interventions
    ADD CONSTRAINT platform_run_interventions_kind_check
    CHECK (kind IN ('context','decision','approval','takeover'));

COMMIT;
