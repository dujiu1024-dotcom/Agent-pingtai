from __future__ import annotations

import unittest
from datetime import datetime, timezone

from agent_platform.graph.governance import GraphEntityStatus, GovernedGraphEntity
from agent_platform.graph.postgres_governance import _entity, _relation


class PostgresGovernanceSerializationTests(unittest.TestCase):
    def test_entity_row_round_trip_preserves_version_status_and_lineage(self):
        now = datetime.now(timezone.utc)
        entity = _entity({
            "tenant_id": "tenant-a", "entity_id": "doc-a", "entity_type": "document",
            "attributes": {"title": "A"}, "project_id": "project-a",
            "source_refs": ["knowledge://tenant-a/doc-a@3"], "version": 4,
            "status": "archived", "expires_at": None, "merged_into": None,
            "updated_at": now, "updated_by": "operator",
        })
        self.assertEqual(entity.version, 4)
        self.assertIs(entity.status, GraphEntityStatus.ARCHIVED)
        self.assertEqual(entity.source_refs, ("knowledge://tenant-a/doc-a@3",))
        self.assertEqual(entity.attributes["title"], "A")

    def test_entity_json_text_rows_are_decoded(self):
        entity = _entity({
            "tenant_id": "tenant-a", "entity_id": "doc-a", "entity_type": "document",
            "attributes": '{"title":"A"}', "project_id": None,
            "source_refs": '["knowledge://tenant-a/doc-a@1"]', "version": 1,
            "status": "active", "expires_at": None, "merged_into": None,
            "updated_at": datetime.now(timezone.utc), "updated_by": "system",
        })
        self.assertEqual(entity.attributes["title"], "A")

    def test_relation_row_round_trip_preserves_tombstone(self):
        relation = _relation({
            "tenant_id": "tenant-a", "relation_id": "edge-a",
            "relation_type": "references", "source_entity_id": "doc-a",
            "target_entity_id": "doc-b", "project_id": None,
            "attributes": {}, "source_refs": [], "version": 2,
            "status": "deleted", "updated_at": datetime.now(timezone.utc),
        })
        self.assertIs(relation.status, GraphEntityStatus.DELETED)
        self.assertEqual(relation.version, 2)

    def test_tenant_mismatch_is_rejected_at_entity_contract(self):
        with self.assertRaises(ValueError):
            GovernedGraphEntity(
                tenant_id="tenant-a", entity_id="bad", entity_type="document",
                attributes={}, source_refs=("knowledge://tenant-b/doc@1",),
            )


if __name__ == "__main__":
    unittest.main()
