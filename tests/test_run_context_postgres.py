from __future__ import annotations

import sys
import types
import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch
from uuid import uuid4

from agent_platform.runtime.run_context import (
    PostgresRunContextSnapshotStore,
    RunContextSnapshot,
    RunContextSnapshotService,
)


class _Jsonb:
    def __init__(self, obj):
        self.obj = obj


class _Cursor:
    def __init__(self, row):
        self.row = row

    async def fetchone(self):
        return self.row


class _FakeConnection:
    def __init__(self, database):
        self.database = database

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    @asynccontextmanager
    async def transaction(self):
        yield self

    async def execute(self, query, params=()):
        if "pg_advisory_xact_lock" in query:
            return _Cursor(None)
        if "SELECT revision FROM platform_run_context_snapshots" in query:
            tenant_id, run_id = params
            rows = [
                row for row in self.database
                if row["tenant_id"] == tenant_id and row["run_id"] == run_id
            ]
            latest = max(rows, key=lambda row: row["revision"]) if rows else None
            return _Cursor({"revision": latest["revision"]} if latest else None)
        if "INSERT INTO platform_run_context_snapshots" in query:
            tenant_id, run_id, revision, snapshot_id, context, references, digest = params
            self.database.append({
                "tenant_id": tenant_id, "run_id": run_id, "revision": revision,
                "snapshot_id": snapshot_id, "context_payload": context.obj,
                "references_payload": references.obj, "digest": digest,
            })
            return _Cursor(None)
        if "SELECT tenant_id, run_id, revision, snapshot_id" in query:
            tenant_id, run_id = params
            rows = [
                row for row in self.database
                if row["tenant_id"] == tenant_id and row["run_id"] == run_id
            ]
            latest = max(rows, key=lambda row: row["revision"]) if rows else None
            return _Cursor(latest)
        raise AssertionError(f"unexpected SQL: {query}")


class PostgresRunContextSnapshotStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.database = []
        async def connect():
            return _FakeConnection(self.database)
        self.store = PostgresRunContextSnapshotStore(connection_factory=connect)
        psycopg = types.ModuleType("psycopg")
        psycopg.__path__ = []
        psycopg_types = types.ModuleType("psycopg.types")
        psycopg_types.__path__ = []
        psycopg_json = types.ModuleType("psycopg.types.json")
        psycopg_json.Jsonb = _Jsonb
        self.modules = patch.dict(sys.modules, {
            "psycopg": psycopg,
            "psycopg.types": psycopg_types,
            "psycopg.types.json": psycopg_json,
        })
        self.modules.start()
        self.addCleanup(self.modules.stop)

    async def test_append_and_latest_round_trip_durable_snapshot(self):
        tenant_id = "tenant-a"
        run_id = str(uuid4())
        context = {"objective": "recover", "decisions": ["keep source"]}
        references = ({"ref_id": "policy-1", "pinned": True},)
        digest = RunContextSnapshotService._digest(
            tenant_id, run_id, 1, context, references,
        )
        snapshot = RunContextSnapshot(
            tenant_id, run_id, 1, str(uuid4()), context, references, digest,
        )
        await self.store.append(snapshot)
        restored = await self.store.latest(tenant_id, run_id)
        self.assertEqual(restored, snapshot)
        self.assertIsNone(await self.store.latest("tenant-b", run_id))

    async def test_append_rejects_revision_gap_or_duplicate(self):
        tenant_id = "tenant-a"
        run_id = str(uuid4())
        context = {"objective": "recover"}
        references = ()
        digest = RunContextSnapshotService._digest(
            tenant_id, run_id, 2, context, references,
        )
        snapshot = RunContextSnapshot(
            tenant_id, run_id, 2, str(uuid4()), context, references, digest,
        )
        with self.assertRaisesRegex(ValueError, "revision conflict"):
            await self.store.append(snapshot)

    async def test_append_rejects_tampered_digest_before_persisting(self):
        snapshot = RunContextSnapshot(
            "tenant-a", str(uuid4()), 1, str(uuid4()),
            {"objective": "tampered"}, (), "0" * 64,
        )
        with self.assertRaisesRegex(ValueError, "integrity check"):
            await self.store.append(snapshot)
        self.assertEqual(self.database, [])

    async def test_postgres_store_requires_uuid_run_ids(self):
        with self.assertRaisesRegex(ValueError, "must be UUIDs"):
            await self.store.latest("tenant-a", "run-not-uuid")


if __name__ == "__main__":
    unittest.main()
