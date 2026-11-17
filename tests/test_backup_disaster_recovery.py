"""Tests for backup scheduling, chain integrity, off-site copies and restore drills."""
import unittest
from datetime import datetime, timedelta, timezone

from agent_platform.deployment.backups import (
    BackupCatalog, BackupCoordinator, BackupKind, BackupPolicy, BackupArtifact,
    RestoreDrillState, begin_restore_drill, meets_rpo,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.policy = BackupPolicy()
        self.catalog = BackupCatalog()
        self.calls = []
        def snapshot(tenant, resource, kind, parent):
            self.calls.append((tenant, resource, kind, parent))
            return (f"s3://primary/{len(self.calls)}", f"sha256:{len(self.calls)}", 128)
        self.coordinator = BackupCoordinator(
            self.policy, self.catalog, snapshot, lambda uri: uri.replace("primary", "offsite")
        )

    def test_full_then_incremental_and_offsite_replication(self):
        full = self.coordinator.run("t1", "db", BackupKind.FULL, NOW)
        incremental = self.coordinator.run(
            "t1", "db", BackupKind.INCREMENTAL, NOW + timedelta(hours=1)
        )
        self.assertEqual(incremental.parent_backup_id, full.backup_id)
        self.assertTrue(full.offsite_uri.startswith("s3://offsite/"))
        self.assertEqual([item.backup_id for item in self.catalog.chain(incremental.backup_id)],
                         [full.backup_id, incremental.backup_id])

    def test_incremental_without_full_rejected(self):
        with self.assertRaises(ValueError):
            self.coordinator.run("t1", "db", BackupKind.INCREMENTAL, NOW)

    def test_chain_cannot_cross_tenant(self):
        self.catalog.add(BackupArtifact("f", "t1", "db", BackupKind.FULL, NOW,
                                        "sum", "s3://full"))
        with self.assertRaises(ValueError):
            self.catalog.add(BackupArtifact("i", "t2", "db", BackupKind.INCREMENTAL,
                                            NOW + timedelta(seconds=1), "sum", "s3://inc",
                                            parent_backup_id="f"))

    def test_offsite_failure_does_not_publish_catalog_entry(self):
        coordinator = BackupCoordinator(
            self.policy, self.catalog,
            lambda *_: ("s3://primary/x", "sum", 1), lambda _: ""
        )
        with self.assertRaises(RuntimeError):
            coordinator.run("t1", "db", BackupKind.FULL, NOW)
        self.assertEqual(self.catalog.artifacts, {})

    def test_restore_drill_requires_complete_chain_and_meets_rto(self):
        full = self.coordinator.run("t1", "db", BackupKind.FULL, NOW)
        drill = begin_restore_drill(self.catalog, full.backup_id, NOW)
        drill.finish(NOW + timedelta(minutes=30), "schema and row-count checks passed",
                     self.policy)
        self.assertEqual(drill.state, RestoreDrillState.VERIFIED)

    def test_restore_drill_fails_when_rto_exceeded(self):
        full = self.coordinator.run("t1", "db", BackupKind.FULL, NOW)
        drill = begin_restore_drill(self.catalog, full.backup_id, NOW)
        drill.finish(NOW + timedelta(hours=5), "restore verified", self.policy)
        self.assertEqual(drill.state, RestoreDrillState.FAILED)
        self.assertEqual(drill.failure_reason, "rto_exceeded")

    def test_rpo_and_retention_pruning(self):
        full = self.coordinator.run("t1", "db", BackupKind.FULL, NOW)
        self.assertTrue(meets_rpo(full, NOW + timedelta(minutes=10), self.policy))
        removed = self.catalog.prune(NOW + timedelta(days=31), self.policy)
        self.assertIn(full.backup_id, removed)
        self.assertIsNone(self.catalog.latest("t1", "db"))

    def test_restore_drill_requires_evidence(self):
        full = self.coordinator.run("t1", "db", BackupKind.FULL, NOW)
        drill = begin_restore_drill(self.catalog, full.backup_id, NOW)
        with self.assertRaises(ValueError):
            drill.finish(NOW + timedelta(minutes=1), " ", self.policy)


if __name__ == "__main__":
    unittest.main()
