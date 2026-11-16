from __future__ import annotations

import asyncio
import os
import unittest
from pathlib import Path
from uuid import uuid4

DSN = os.environ.get("AGENT_PLATFORM_TEST_DSN")


@unittest.skipUnless(DSN, "AGENT_PLATFORM_TEST_DSN is not configured")
class FileAssetVersionPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from psycopg import AsyncConnection
        from psycopg.rows import dict_row

        from agent_platform.control_plane.postgres import PostgresSessionRepository
        from agent_platform.control_plane.sessions_assets import InMemoryObjectStorage, SessionAssetService

        self.tenant_id = "asset-version-it-" + uuid4().hex
        async with await AsyncConnection.connect(DSN, autocommit=True) as conn:
            for path in sorted(Path("agent_platform/storage/postgres").glob("*.sql")):
                await conn.execute(path.read_text(), prepare=False)
        async with await AsyncConnection.connect(DSN) as conn:
            await conn.execute(
                "INSERT INTO platform_tenants (tenant_id,display_name) VALUES (%s,%s)",
                (self.tenant_id, "File Asset Version Integration"),
            )
            await conn.commit()

        async def connect():
            return await AsyncConnection.connect(DSN, row_factory=dict_row)

        self.repository = PostgresSessionRepository(
            tenant_id=self.tenant_id, connection_factory=connect,
        )
        self.storage = InMemoryObjectStorage()
        self.service = SessionAssetService(self.repository, self.storage)

    async def test_version_history_persists_and_old_bytes_remain_readable(self):
        asset = await self.service.upload_asset(
            tenant_id=self.tenant_id, owner_id="user-a", filename="report.txt",
            media_type="text/plain", content=b"version one",
        )
        second = await self.service.create_asset_version(
            tenant_id=self.tenant_id, asset_id=asset.asset_id,
            content=b"version two", expected_version=1,
        )
        self.assertEqual(second.version, 2)

        current = await self.repository.get_asset(self.tenant_id, asset.asset_id)
        self.assertEqual(current.version, 2)
        history = await self.repository.list_asset_versions(self.tenant_id, asset.asset_id)
        self.assertEqual([item.version for item in history], [1, 2])
        old, old_bytes = await self.service.download_asset_version(
            tenant_id=self.tenant_id, asset_id=asset.asset_id, version=1,
        )
        self.assertEqual(old.sha256, asset.sha256)
        self.assertEqual(old_bytes, b"version one")

    async def test_concurrent_updates_use_compare_and_swap(self):
        asset = await self.service.upload_asset(
            tenant_id=self.tenant_id, owner_id="user-a", filename="race.txt",
            media_type="text/plain", content=b"base",
        )
        results = await asyncio.gather(
            self.service.create_asset_version(
                tenant_id=self.tenant_id, asset_id=asset.asset_id,
                content=b"writer one", expected_version=1,
            ),
            self.service.create_asset_version(
                tenant_id=self.tenant_id, asset_id=asset.asset_id,
                content=b"writer two", expected_version=1,
            ),
            return_exceptions=True,
        )
        self.assertEqual(sum(not isinstance(item, Exception) for item in results), 1)
        latest = await self.repository.get_asset(self.tenant_id, asset.asset_id)
        self.assertEqual(latest.version, 2)
        history = await self.repository.list_asset_versions(self.tenant_id, asset.asset_id)
        self.assertEqual([item.version for item in history], [1, 2])


if __name__ == "__main__":
    unittest.main()
