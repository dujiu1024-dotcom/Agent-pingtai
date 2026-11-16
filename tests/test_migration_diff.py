from __future__ import annotations

import unittest

from agent_platform.data_governance.diff import compare_migration_rows


class MigrationDiffTests(unittest.TestCase):
    def test_reports_missing_and_changed_rows_deterministically(self):
        report = compare_migration_rows(
            source_rows=[{"id": "1", "value": "same"}, {"id": "2", "value": "old"}],
            target_rows=[{"id": "1", "value": "same"}, {"id": "3", "value": "extra"}],
            key_field="id",
        )
        self.assertEqual(report.missing_in_target, ("2",))
        self.assertEqual(report.missing_in_source, ("3",))
        self.assertEqual(report.changed, ())
        self.assertFalse(report.matched)

    def test_reports_payload_changes_and_stable_checksums(self):
        source = [{"id": "1", "value": "old"}]
        target = [{"id": "1", "value": "new"}]
        first = compare_migration_rows(source_rows=source, target_rows=target, key_field="id")
        second = compare_migration_rows(source_rows=source, target_rows=target, key_field="id")
        self.assertEqual(first.changed, ("1",))
        self.assertEqual(first.source_checksum, second.source_checksum)
        self.assertEqual(first.target_checksum, second.target_checksum)

    def test_rejects_missing_and_duplicate_keys(self):
        with self.assertRaises(ValueError):
            compare_migration_rows(source_rows=[{"value": 1}], target_rows=[], key_field="id")
        with self.assertRaises(ValueError):
            compare_migration_rows(source_rows=[{"id": "1"}, {"id": "1"}], target_rows=[], key_field="id")


if __name__ == "__main__":
    unittest.main()
