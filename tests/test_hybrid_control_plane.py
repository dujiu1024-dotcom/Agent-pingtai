from __future__ import annotations

import unittest

from agent_platform.resilience.hybrid_control_plane import (
    ControlPlaneMode,
    ControlPlaneReconciler,
    OfflinePolicySynchronizer,
    PolicySnapshotRejected,
    RunNodeHandoff,
    SignedPolicySnapshot,
)


class MemoryPolicyStore:
    def __init__(self) -> None:
        self.rows: dict[str, SignedPolicySnapshot] = {}

    async def get(self, tenant_id: str) -> SignedPolicySnapshot | None:
        return self.rows.get(tenant_id)

    async def put_if_newer(
        self, snapshot: SignedPolicySnapshot, *, expected_revision: int | None,
    ) -> bool:
        current = self.rows.get(snapshot.tenant_id)
        actual = current.revision if current else None
        if actual != expected_revision:
            return False
        if current and (
            snapshot.revision <= current.revision
            or snapshot.revocation_epoch < current.revocation_epoch
        ):
            return False
        self.rows[snapshot.tenant_id] = snapshot
        return True


def snapshot(revision: int = 1, epoch: int = 1) -> SignedPolicySnapshot:
    return SignedPolicySnapshot(
        tenant_id="tenant-a", revision=revision, policy_version=f"v{revision}",
        issued_at=100, expires_at=200, revocation_epoch=epoch,
        payload={"allow": ["read"]}, signature="verified-signature",
    )


class HybridControlPlaneTests(unittest.IsolatedAsyncioTestCase):
    async def test_policy_sync_advances_monotonically_and_is_idempotent(self) -> None:
        store = MemoryPolicyStore()
        events: list[dict[str, object]] = []
        sync = OfflinePolicySynchronizer(
            store=store, verify_signature=lambda value: value.signature == "verified-signature",
            clock=lambda: 150, audit=lambda event: events.append(dict(event)),
        )
        first = await sync.synchronize(snapshot(), expected_tenant_id="tenant-a")
        repeated = await sync.synchronize(snapshot(), expected_tenant_id="tenant-a")
        self.assertTrue(first.changed)
        self.assertFalse(repeated.changed)
        self.assertEqual(store.rows["tenant-a"].revision, 1)
        self.assertEqual(len(events), 2)

    async def test_policy_sync_rejects_tenant_signature_and_rollback(self) -> None:
        store = MemoryPolicyStore()
        sync = OfflinePolicySynchronizer(
            store=store, verify_signature=lambda value: value.signature == "verified-signature",
            clock=lambda: 150, audit=lambda _event: None,
        )
        with self.assertRaises(PolicySnapshotRejected):
            await sync.synchronize(snapshot(), expected_tenant_id="other-tenant")
        with self.assertRaises(PolicySnapshotRejected):
            await sync.synchronize(
                SignedPolicySnapshot(
                    tenant_id="tenant-a", revision=1, policy_version="v1",
                    issued_at=100, expires_at=200, revocation_epoch=1,
                    payload={}, signature="bad",
                ),
                expected_tenant_id="tenant-a",
            )
        await sync.synchronize(snapshot(2, 2), expected_tenant_id="tenant-a")
        with self.assertRaises(PolicySnapshotRejected):
            await sync.synchronize(snapshot(1, 1), expected_tenant_id="tenant-a")

    async def test_policy_sync_rejects_revocation_epoch_rollback(self) -> None:
        store = MemoryPolicyStore()
        sync = OfflinePolicySynchronizer(
            store=store, verify_signature=lambda _value: True,
            clock=lambda: 150, audit=lambda _event: None,
        )
        await sync.synchronize(snapshot(4, 9), expected_tenant_id="tenant-a")
        with self.assertRaises(PolicySnapshotRejected):
            await sync.synchronize(snapshot(5, 8), expected_tenant_id="tenant-a")

    async def test_reconcile_applies_revocations_before_replay_and_resume(self) -> None:
        calls: list[str] = []

        async def step(name: str) -> None:
            calls.append(name)

        reconciler = ControlPlaneReconciler(
            synchronize_policy=lambda: step("sync"),
            apply_revocations=lambda: step("revocations"),
            reconcile_license=lambda: step("license"),
            replay_outbox=lambda: step("replay"),
            resume_paused_runs=lambda: step("resume"),
            audit=lambda event: calls.append(str(event["event"])),
        )
        self.assertEqual(
            await reconciler.control_plane_unavailable(reason="network partition"),
            ControlPlaneMode.DEGRADED,
        )
        self.assertEqual(await reconciler.reconcile(), ControlPlaneMode.ONLINE)
        self.assertEqual(
            calls[-5:], ["sync", "revocations", "license", "replay", "resume"],
        )

    async def test_reconcile_failure_remains_degraded_and_does_not_resume(self) -> None:
        calls: list[str] = []

        async def fail() -> None:
            calls.append("revocations")
            raise RuntimeError("revocation store unavailable")

        reconciler = ControlPlaneReconciler(
            synchronize_policy=lambda: calls.append("sync"),
            apply_revocations=fail,
            reconcile_license=lambda: calls.append("license"),
            replay_outbox=lambda: calls.append("replay"),
            resume_paused_runs=lambda: calls.append("resume"),
            audit=lambda event: calls.append(str(event["event"])),
        )
        await reconciler.control_plane_unavailable(reason="partition")
        with self.assertRaises(RuntimeError):
            await reconciler.reconcile()
        self.assertEqual(reconciler.mode, ControlPlaneMode.DEGRADED)
        self.assertEqual(calls[:2], ["control_plane_degraded", "control_plane_reconciling"])
        self.assertNotIn("resume", calls)

    async def test_run_handoff_fences_before_start_and_supports_failback(self) -> None:
        calls: list[str] = []
        fenced: set[str] = set()

        async def stop(node: str) -> None:
            calls.append(f"stop:{node}")

        async def fence(node: str) -> None:
            calls.append(f"fence:{node}")
            fenced.add(node)

        async def confirm(node: str) -> bool:
            calls.append(f"confirm:{node}")
            return node in fenced

        async def start(node: str) -> None:
            calls.append(f"start:{node}")

        handoff = RunNodeHandoff(
            stop_worker=stop, fence_node=fence, confirm_fenced=confirm,
            start_worker=start, audit=lambda _event: None,
        )
        await handoff.failover(primary="node-a", recovery="node-b")
        await handoff.failback(recovery="node-b", primary="node-a")
        self.assertEqual(
            calls,
            [
                "stop:node-a", "fence:node-a", "confirm:node-a", "start:node-b",
                "stop:node-b", "fence:node-b", "confirm:node-b", "start:node-a",
            ],
        )

    async def test_run_handoff_refuses_unfenced_source(self) -> None:
        calls: list[str] = []
        handoff = RunNodeHandoff(
            stop_worker=lambda node: calls.append(f"stop:{node}"),
            fence_node=lambda node: calls.append(f"fence:{node}"),
            confirm_fenced=lambda _node: False,
            start_worker=lambda node: calls.append(f"start:{node}"),
            audit=lambda _event: None,
        )
        with self.assertRaisesRegex(RuntimeError, "not fenced"):
            await handoff.failover(primary="node-a", recovery="node-b")
        self.assertNotIn("start:node-b", calls)


if __name__ == "__main__":
    unittest.main()
