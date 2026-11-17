from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_platform.data_governance.migration_audit import (
    MigrationAuditError,
    append_migration_audit_event,
    list_migration_audit_events,
    verify_migration_audit_chain,
)


class MigrationAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "audit.db"

    def tearDown(self):
        self.temp.cleanup()

    def append(self, **overrides):
        values = {
            "tenant_id": "tenant-a",
            "actor_id": "operator-7",
            "batch_id": "batch-2026-11-17",
            "action": "migration_failed",
            "result": "failed",
            "attempt": 2,
            "failed_items": ["users:42", "documents:19"],
            "evidence_refs": ["s3://audit/batch-17/diff.json", "run:ci-123"],
            "details": {"error_code": "TRANSFORM_INVALID", "source_table": "users"},
        }
        values.update(overrides)
        return append_migration_audit_event(self.database, **values)

    def test_records_actor_scope_result_failures_retry_and_evidence(self):
        event = self.append()
        self.assertEqual(event.actor_id, "operator-7")
        self.assertEqual(event.tenant_id, "tenant-a")
        self.assertEqual(event.result, "failed")
        self.assertEqual(event.attempt, 2)
        self.assertEqual(event.failed_items, ("users:42", "documents:19"))
        self.assertEqual(event.evidence_refs[1], "run:ci-123")
        self.assertEqual(event.previous_hash, "0" * 64)

    def test_hash_chain_links_events_and_is_tenant_scoped(self):
        first = self.append()
        second = self.append(action="migration_retry", result="retrying", attempt=3)
        other = self.append(tenant_id="tenant-b", batch_id="batch-b")
        self.assertEqual(second.previous_hash, first.event_hash)
        self.assertEqual(other.previous_hash, "0" * 64)
        self.assertEqual(
            [item.event_id for item in list_migration_audit_events(
                self.database, tenant_id="tenant-a"
            )],
            [first.event_id, second.event_id],
        )
        self.assertTrue(verify_migration_audit_chain(
            self.database, tenant_id="tenant-a"
        )["valid"])
        self.assertTrue(verify_migration_audit_chain(
            self.database, tenant_id="tenant-b"
        )["valid"])

    def test_audit_rows_cannot_be_updated_or_deleted(self):
        event = self.append()
        with sqlite3.connect(self.database) as db:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                db.execute(
                    "UPDATE migration_audit_events SET result='completed' WHERE event_id=?",
                    (event.event_id,),
                )
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                db.execute(
                    "DELETE FROM migration_audit_events WHERE event_id=?",
                    (event.event_id,),
                )

    def test_chain_verification_detects_out_of_band_tampering(self):
        self.append()
        with sqlite3.connect(self.database) as db:
            db.execute("DROP TRIGGER migration_audit_no_update")
            db.execute(
                "UPDATE migration_audit_events SET result='completed' WHERE tenant_id='tenant-a'"
            )
        result = verify_migration_audit_chain(self.database, tenant_id="tenant-a")
        self.assertFalse(result["valid"])
        self.assertEqual(result["reason"], "event_hash_mismatch")

    def test_query_requires_tenant_and_never_returns_other_tenants(self):
        self.append()
        self.append(tenant_id="tenant-b", batch_id="batch-b")
        self.assertEqual(
            len(list_migration_audit_events(self.database, tenant_id="tenant-a")), 1
        )
        self.assertEqual(
            len(list_migration_audit_events(self.database, tenant_id="tenant-a",
                                            batch_id="batch-b")), 0
        )

    def test_invalid_scope_action_attempt_and_metadata_fail_closed(self):
        invalid = (
            {"tenant_id": ""},
            {"actor_id": "../root"},
            {"action": "unknown_action"},
            {"result": "maybe"},
            {"attempt": True},
            {"details": {"not_json": float("nan")}},
        )
        for override in invalid:
            with self.subTest(override=override):
                with self.assertRaises((ValueError, TypeError)):
                    self.append(**override)
        self.assertEqual(verify_migration_audit_chain(
            self.database, tenant_id="tenant-a"
        )["events_checked"], 0)

    def test_tenant_batch_query_and_limit_are_validated(self):
        self.append()
        with self.assertRaises(ValueError):
            list_migration_audit_events(self.database, tenant_id="tenant-a", limit=0)
        with self.assertRaises(ValueError):
            list_migration_audit_events(self.database, tenant_id="tenant-a",
                                        batch_id="../other")


if __name__ == "__main__":
    unittest.main()
