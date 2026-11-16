from __future__ import annotations

import unittest

from agent_platform.data_governance.lifecycle import (
    MigrationBatch, MigrationRow, PostgresMigrationRepository,
)


class FakeCursor:
    def __init__(self, row=None):
        self.row = row
    async def fetchone(self):
        return self.row


class FakeMigrationConnection:
    def __init__(self, db):
        self.db = db
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        return False
    def transaction(self):
        return self
    async def execute(self, query, params=()):
        sql = " ".join(query.split())
        if sql.startswith("INSERT INTO platform_data_migration_batches"):
            tenant, batch, source, target, obj = params
            self.db.batches[(tenant,batch)] = {
                "status": "planned", "source": source, "target": target, "object": obj,
            }
            return FakeCursor()
        if sql.startswith("UPDATE platform_data_migration_batches SET status='running'"):
            tenant, batch = params
            item = self.db.batches.get((tenant,batch))
            if item and item["status"] in {"planned","running"}:
                item["status"] = "running"
                return FakeCursor({"batch_id": batch})
            return FakeCursor()
        if sql.startswith("SELECT payload_hash,status FROM platform_data_migration_rows"):
            tenant, batch, source_key = params
            return FakeCursor(self.db.rows.get((tenant,batch,source_key)))
        if sql.startswith("INSERT INTO platform_data_migration_rows"):
            tenant,batch,source,target,digest,status,error = params
            self.db.rows[(tenant,batch,source)] = {
                "payload_hash": digest, "status": status, "target_key": target,
            }
            return FakeCursor()
        if sql.startswith("UPDATE platform_data_migration_batches SET source_count"):
            source_count,target_count,source_hash,target_hash,status,finished,tenant,batch = params
            item = self.db.batches.get((tenant,batch))
            if item and item["status"] == "running":
                item.update(status=status, source_count=source_count, target_count=target_count,
                            source_checksum=source_hash, target_checksum=target_hash)
                return FakeCursor({"batch_id": batch})
            return FakeCursor()
        if sql.startswith("UPDATE platform_data_migration_batches SET status='switched'"):
            tenant,batch = params
            item = self.db.batches.get((tenant,batch))
            if item and item["status"] == "validated":
                item["status"] = "switched"
                return FakeCursor({"batch_id": batch})
            return FakeCursor()
        if sql.startswith("UPDATE platform_data_migration_batches SET status='rolled_back'"):
            reason,tenant,batch = params
            item = self.db.batches.get((tenant,batch))
            if item and item["status"] == "switched":
                item.update(status="rolled_back", rollback_reason=reason)
                return FakeCursor({"batch_id": batch})
            return FakeCursor()
        raise AssertionError(f"unhandled SQL: {sql}")


class FakeMigrationDb:
    def __init__(self):
        self.batches = {}
        self.rows = {}
    async def connect(self):
        return FakeMigrationConnection(self)


class MigrationRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_batch_must_validate_before_switch_and_rollback_is_recorded(self):
        db = FakeMigrationDb()
        repository = PostgresMigrationRepository(db.connect)
        batch = MigrationBatch(
            tenant_id="tenant-a", batch_id="batch-1", source_system="sqlite",
            target_system="postgres", object_type="memory", status="planned",
        )
        await repository.create_batch(batch)
        self.assertTrue(await repository.start_batch("tenant-a", "batch-1"))
        self.assertFalse(await repository.switch_batch("tenant-a", "batch-1"))
        self.assertTrue(await repository.validate_batch(
            "tenant-a", "batch-1", source_count=10, target_count=10,
            source_checksum="same", target_checksum="same",
        ))
        self.assertTrue(await repository.switch_batch("tenant-a", "batch-1"))
        self.assertTrue(await repository.rollback_batch(
            "tenant-a", "batch-1", reason="post-switch verification failed",
        ))
        self.assertEqual(db.batches[("tenant-a","batch-1")]["rollback_reason"],
                         "post-switch verification failed")

    async def test_migration_row_is_idempotent_and_rejects_changed_payload(self):
        db = FakeMigrationDb()
        repository = PostgresMigrationRepository(db.connect)
        await repository.create_batch(MigrationBatch(
            tenant_id="tenant-a", batch_id="batch-2", source_system="sqlite",
            target_system="postgres", object_type="conversation", status="planned",
        ))
        row = MigrationRow(
            tenant_id="tenant-a", batch_id="batch-2", source_key="row-1",
            target_key="pg-1", payload_hash="hash-1", status="pending",
        )
        await repository.record_row(row)
        await repository.record_row(MigrationRow(
            tenant_id="tenant-a", batch_id="batch-2", source_key="row-1",
            target_key="pg-1", payload_hash="hash-1", status="imported",
        ))
        with self.assertRaises(ValueError):
            await repository.record_row(MigrationRow(
                tenant_id="tenant-a", batch_id="batch-2", source_key="row-1",
                target_key="pg-1", payload_hash="changed", status="imported",
            ))
        with self.assertRaises(ValueError):
            await repository.record_row(MigrationRow(
                tenant_id="tenant-a", batch_id="batch-2", source_key="row-1",
                target_key="pg-1", payload_hash="hash-1", status="pending",
            ))


if __name__ == "__main__":
    unittest.main()
