from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from agent_platform.resilience.hybrid_control_plane import (
    LocalFilePolicySnapshotStore,
    SignedPolicySnapshot,
)


def make_snapshot(
    revision: int = 1, *, tenant_id: str = "tenant-a", epoch: int = 1,
) -> SignedPolicySnapshot:
    return SignedPolicySnapshot(
        tenant_id=tenant_id,
        revision=revision,
        policy_version=f"policy-v{revision}",
        issued_at=100,
        expires_at=200,
        revocation_epoch=epoch,
        payload={"permissions": ["read"], "limits": {"runs": 4}},
        signature=f"signature-{revision}",
    )


class LocalFilePolicySnapshotStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.directory = Path(self.temporary_directory.name)
        self.store = LocalFilePolicySnapshotStore(self.directory)

    async def test_round_trip_survives_store_recreation(self) -> None:
        snapshot = make_snapshot()
        self.assertTrue(await self.store.put_if_newer(snapshot, expected_revision=None))

        reopened = LocalFilePolicySnapshotStore(self.directory)
        self.assertEqual(await reopened.get("tenant-a"), snapshot)

    async def test_compare_and_swap_rejects_stale_expected_revision(self) -> None:
        self.assertTrue(await self.store.put_if_newer(make_snapshot(), expected_revision=None))
        self.assertFalse(
            await self.store.put_if_newer(make_snapshot(2), expected_revision=None)
        )
        self.assertFalse(
            await self.store.put_if_newer(make_snapshot(2), expected_revision=7)
        )
        self.assertEqual((await self.store.get("tenant-a")).revision, 1)

    async def test_revision_and_revocation_epoch_are_monotonic(self) -> None:
        self.assertTrue(await self.store.put_if_newer(make_snapshot(3, epoch=8), expected_revision=None))
        self.assertFalse(
            await self.store.put_if_newer(
                make_snapshot(4, epoch=7), expected_revision=3,
            )
        )
        self.assertFalse(
            await self.store.put_if_newer(
                make_snapshot(3, epoch=8), expected_revision=3,
            )
        )
        self.assertTrue(
            await self.store.put_if_newer(
                make_snapshot(4, epoch=8), expected_revision=3,
            )
        )
        self.assertEqual((await self.store.get("tenant-a")).revision, 4)

    async def test_concurrent_writers_with_same_expected_revision_have_one_winner(self) -> None:
        results = await asyncio.gather(
            self.store.put_if_newer(make_snapshot(2), expected_revision=None),
            self.store.put_if_newer(make_snapshot(3), expected_revision=None),
        )
        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), 1)
        current = await self.store.get("tenant-a")
        self.assertIn(current.revision, (2, 3))

    async def test_tenants_are_isolated_and_not_used_as_file_paths(self) -> None:
        await self.store.put_if_newer(make_snapshot(tenant_id="../tenant-a"), expected_revision=None)
        await self.store.put_if_newer(make_snapshot(tenant_id="tenant-a"), expected_revision=None)
        self.assertEqual((await self.store.get("../tenant-a")).tenant_id, "../tenant-a")
        self.assertEqual((await self.store.get("tenant-a")).tenant_id, "tenant-a")
        self.assertEqual(len(list(self.directory.glob("*.json"))), 2)

    async def test_corrupt_snapshot_fails_closed(self) -> None:
        await self.store.put_if_newer(make_snapshot(), expected_revision=None)
        snapshot_path = next(self.directory.glob("*.json"))
        snapshot_path.write_text("{not-json", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "corrupt"):
            await self.store.get("tenant-a")

    async def test_non_json_payload_is_rejected_without_persisting(self) -> None:
        snapshot = SignedPolicySnapshot(
            tenant_id="tenant-a", revision=1, policy_version="v1",
            issued_at=100, expires_at=200, revocation_epoch=1,
            payload={"not_json": object()}, signature="signature",
        )
        with self.assertRaisesRegex(ValueError, "finite JSON"):
            await self.store.put_if_newer(snapshot, expected_revision=None)
        self.assertIsNone(await self.store.get("tenant-a"))

    async def test_invalid_tenant_and_expected_revision_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            await self.store.get(" ")
        with self.assertRaises(ValueError):
            await self.store.put_if_newer(make_snapshot(), expected_revision=0)


if __name__ == "__main__":
    unittest.main()
