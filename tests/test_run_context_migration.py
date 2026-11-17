from pathlib import Path
import unittest


class RunContextSnapshotMigrationTests(unittest.TestCase):
    def test_latest_migration_upgrades_legacy_snapshot_columns(self):
        migration = (
            Path(__file__).parents[1]
            / "agent_platform/storage/postgres/060_run_context_snapshots.sql"
        ).read_text(encoding="utf-8")
        normalized = " ".join(migration.lower().split())

        self.assertIn("add column if not exists context_payload jsonb", normalized)
        self.assertIn("add column if not exists references_payload jsonb", normalized)
        self.assertIn("set context_payload = context where context_payload is null", normalized)
        self.assertIn(
            "set references_payload = reference_window where references_payload is null",
            normalized,
        )
        self.assertIn("alter column context_payload set not null", normalized)
        self.assertIn("alter column references_payload set not null", normalized)
        self.assertTrue(normalized.rstrip().endswith("commit;"))
        self.assertNotIn("), created_at", normalized)

    def test_migration_keeps_adapter_payload_column_names(self):
        migration = (
            Path(__file__).parents[1]
            / "agent_platform/storage/postgres/060_run_context_snapshots.sql"
        ).read_text(encoding="utf-8").lower()

        self.assertIn("context_payload", migration)
        self.assertIn("references_payload", migration)
        self.assertIn("platform_run_context_snapshots", migration)


if __name__ == "__main__":
    unittest.main()
