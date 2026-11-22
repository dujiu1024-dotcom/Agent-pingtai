from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from uuid import UUID

from agent_platform.knowledge.acl_invalidation_events import (
    ACLInvalidationReason,
    ACLInvalidationResourceType,
    KnowledgeACLInvalidationEvent,
)


class KnowledgeACLInvalidationEventTests(unittest.TestCase):
    event: KnowledgeACLInvalidationEvent

    def setUp(self) -> None:
        self.event = KnowledgeACLInvalidationEvent(
            event_id=42,
            tenant_id=" tenant-a ",
            resource_type=ACLInvalidationResourceType.CHUNK,
            resource_id=UUID("00000000-0000-0000-0000-000000000042"),
            reason=ACLInvalidationReason.ACL_CHANGED,
            created_at=datetime(2026, 11, 22, 12, 30, tzinfo=timezone.utc),
        )

    def test_dict_round_trip_uses_canonical_payload(self) -> None:
        payload = self.event.to_dict()
        self.assertEqual(payload, {
            "event_id": 42,
            "tenant_id": "tenant-a",
            "resource_type": "chunk",
            "resource_id": "00000000-0000-0000-0000-000000000042",
            "reason": "acl_changed",
            "created_at": "2026-11-22T12:30:00+00:00",
        })
        self.assertEqual(KnowledgeACLInvalidationEvent.from_dict(payload), self.event)

    def test_json_round_trip_is_deterministic(self) -> None:
        serialized = self.event.to_json()
        self.assertEqual(serialized, self.event.to_json())
        self.assertEqual(
            KnowledgeACLInvalidationEvent.from_json(serialized), self.event,
        )
        self.assertEqual(json.loads(serialized), self.event.to_dict())

    def test_accepts_utc_z_timestamp(self) -> None:
        payload = self.event.to_dict()
        payload["created_at"] = "2026-11-22T12:30:00Z"
        self.assertEqual(
            KnowledgeACLInvalidationEvent.from_dict(payload).created_at,
            self.event.created_at,
        )

    def test_from_record_accepts_datetime_without_database_dependency(self) -> None:
        record = self.event.to_dict()
        record["created_at"] = self.event.created_at
        self.assertEqual(KnowledgeACLInvalidationEvent.from_record(record), self.event)

    def test_rejects_missing_or_unknown_fields(self) -> None:
        payload = self.event.to_dict()
        with self.assertRaisesRegex(ValueError, "invalid event fields"):
            KnowledgeACLInvalidationEvent.from_dict({**payload, "unexpected": True})
        payload.pop("reason")
        with self.assertRaisesRegex(ValueError, "invalid event fields"):
            KnowledgeACLInvalidationEvent.from_dict(payload)

    def test_rejects_invalid_identifiers_enums_and_timestamp(self) -> None:
        for field, value in (
            ("event_id", True),
            ("event_id", 0),
            ("tenant_id", " "),
            ("resource_type", "user"),
            ("resource_id", "not-a-uuid"),
            ("reason", "unknown"),
            ("created_at", "not-a-date"),
        ):
            payload = self.event.to_dict()
            payload[field] = value
            with self.subTest(field=field, value=value):
                with self.assertRaises(ValueError):
                    KnowledgeACLInvalidationEvent.from_dict(payload)

    def test_constructor_rejects_naive_timestamp(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            KnowledgeACLInvalidationEvent(
                event_id=1, tenant_id="tenant-a",
                resource_type=ACLInvalidationResourceType.DOCUMENT,
                resource_id=self.event.resource_id,
                reason=ACLInvalidationReason.DELETED,
                created_at=datetime(2026, 11, 22),
            )

    def test_rejects_non_object_or_invalid_json(self) -> None:
        with self.assertRaisesRegex(ValueError, "valid JSON"):
            KnowledgeACLInvalidationEvent.from_json("{")
        with self.assertRaisesRegex(ValueError, "object"):
            KnowledgeACLInvalidationEvent.from_json("[]")


if __name__ == "__main__":
    unittest.main()
