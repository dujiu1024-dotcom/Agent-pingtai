from __future__ import annotations

import unittest

from agent_platform.data_governance.lifecycle import MigrationBatch, MigrationRow
from agent_platform.data_governance.runner import MigrationRunner


class FakeLedger:
    def __init__(self):
        self.rows = {}
        self.started = False
        self.validated = False

    async def start_batch(self, tenant_id, batch_id):
        self.started = True
        return True

    async def get_row(self, tenant_id, batch_id, source_key):
        return self.rows.get(source_key)

    async def record_row(self, row: MigrationRow):
        old = self.rows.get(row.source_key)
        if old and old["payload_hash"] != row.payload_hash:
            raise ValueError("payload changed")
        transitions = {
            "pending": {"pending", "imported", "failed"},
            "failed": {"failed", "pending"},
            "imported": {"imported", "validated", "failed"},
            "validated": {"validated"},
        }
        if old and row.status not in transitions[old["status"]]:
            raise ValueError("invalid transition")
        self.rows[row.source_key] = {
            "payload_hash": row.payload_hash,
            "target_key": row.target_key,
            "status": row.status,
        }

    async def validate_batch(self, tenant_id, batch_id, *, source_count, target_count,
                             source_checksum, target_checksum):
        self.validated = source_count == target_count and source_checksum == target_checksum
        return self.validated


class FakeTarget:
    def __init__(self):
        self.rows = {}
        self.fail_once = set()

    async def upsert(self, *, source_key, payload):
        if source_key in self.fail_once:
            self.fail_once.remove(source_key)
            raise TimeoutError("transient target error")
        self.rows[source_key] = {"legacy_id": source_key, "value": payload["value"]}
        return "target-" + source_key

    async def snapshot(self, *, source_keys):
        return [self.rows[key] for key in source_keys if key in self.rows]




class CorruptTarget(FakeTarget):
    async def upsert(self, *, source_key, payload):
        target_key = await super().upsert(source_key=source_key, payload=payload)
        self.rows[source_key]["value"] = "corrupted"
        return target_key


def normalize_target(row):
    return {"id": row["legacy_id"], "value": row["value"]}


def make_batch():
    return MigrationBatch(
        tenant_id="tenant-a", batch_id="batch-1", source_system="sqlite",
        target_system="postgres", object_type="records", status="planned",
    )


class MigrationRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_batch_imports_verifies_and_records_validated_rows(self):
        ledger, target = FakeLedger(), FakeTarget()
        runner = MigrationRunner(ledger=ledger)
        report = await runner.execute(
            batch=make_batch(),
            source_rows=[{"id": "1", "value": "one"}, {"id": "2", "value": "two"}],
            key_field="id", target=target,
            transform=lambda row: {"value": row["value"]},
            normalize_target=normalize_target,
        )
        self.assertTrue(report.validated)
        self.assertTrue(report.diff.matched)
        self.assertEqual(report.imported_count, 2)
        self.assertEqual({row["status"] for row in ledger.rows.values()}, {"validated"})

    async def test_failed_row_is_recorded_and_can_be_retried(self):
        ledger, target = FakeLedger(), FakeTarget()
        target.fail_once.add("2")
        runner = MigrationRunner(ledger=ledger)
        source = [{"id": "1", "value": "one"}, {"id": "2", "value": "two"}]
        first = await runner.execute(
            batch=make_batch(), source_rows=source, key_field="id", target=target,
            transform=lambda row: {"value": row["value"]},
            normalize_target=normalize_target,
        )
        self.assertFalse(first.validated)
        self.assertEqual(first.failed_rows, {"2": "TimeoutError"})
        second = await runner.execute(
            batch=make_batch(), source_rows=source, key_field="id", target=target,
            transform=lambda row: {"value": row["value"]},
            normalize_target=normalize_target,
        )
        self.assertTrue(second.validated)
        self.assertTrue(second.diff.matched)

    async def test_duplicate_keys_are_rejected_before_target_writes(self):
        ledger, target = FakeLedger(), FakeTarget()
        with self.assertRaisesRegex(ValueError, "duplicate"):
            await MigrationRunner(ledger=ledger).execute(
                batch=make_batch(),
                source_rows=[{"id": "1"}, {"id": "1"}], key_field="id",
                target=target, transform=lambda row: row,
                normalize_target=lambda row: row,
            )
        self.assertEqual(target.rows, {})

    async def test_mismatched_target_snapshot_does_not_validate_batch(self):
        ledger, target = FakeLedger(), CorruptTarget()
        report = await MigrationRunner(ledger=ledger).execute(
            batch=make_batch(), source_rows=[{"id": "1", "value": "right"}],
            key_field="id", target=target,
            transform=lambda row: {"value": row["value"]},
            normalize_target=normalize_target,
        )
        self.assertFalse(report.validated)
        self.assertFalse(report.diff.matched)
        self.assertFalse(ledger.validated)


if __name__ == "__main__":
    unittest.main()
