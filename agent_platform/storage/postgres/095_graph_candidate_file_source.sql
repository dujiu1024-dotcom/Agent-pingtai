-- Extend the graph candidate source contract to parsed #8 file assets.
-- Raw file bytes remain outside the graph extraction pipeline; only parser output
-- represented as a versioned parsed_file source record is eligible.
BEGIN;

ALTER TABLE platform_graph_extraction_candidates
    DROP CONSTRAINT IF EXISTS platform_graph_extraction_candidates_source_domain_check;

ALTER TABLE platform_graph_extraction_candidates
    ADD CONSTRAINT platform_graph_extraction_candidates_source_domain_check
    CHECK (source_domain IN ('knowledge', 'memory', 'blackboard', 'file'));

COMMIT;
