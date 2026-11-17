"""Tests for durable Run context snapshot assembly and recovery reconstruction."""
import unittest

from agent_platform.runtime.run_context import (
    InMemoryRunContextSnapshotStore,
    RunContextSnapshotService,
)


class RunContextSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_context_snapshot_rebuilds_latest_revision_and_is_defensive(self) -> None:
        store = InMemoryRunContextSnapshotStore()
        service = RunContextSnapshotService(
            tenant_id="tenant-a", store=store, reference_window_size=3,
        )
        first = await service.ensure_initial(
            tenant_id="tenant-a", run_id="run-a",
            initial_context={"prompt": "summarize", "objective": "summary"},
        )
        self.assertEqual(first.revision, 1)
        await service.capture(
            tenant_id="tenant-a", run_id="run-a",
            updates={"decisions": ["use source A"]},
            references=[
                {"ref_id": "old", "type": "document"},
                {"ref_id": "pinned", "type": "policy", "pinned": True},
                {"ref_id": "new-1"}, {"ref_id": "new-2"},
            ],
        )
        rebuilt = await service.rebuild(tenant_id="tenant-a", run_id="run-a")
        self.assertEqual(rebuilt["prompt"], "summarize")
        self.assertEqual(rebuilt["objective"], "summary")
        self.assertEqual(rebuilt["decisions"], ["use source A"])
        self.assertEqual(
            [ref["ref_id"] for ref in rebuilt["references"]],
            ["pinned", "new-1", "new-2"],
        )
        rebuilt["decisions"].append("mutated outside store")
        again = await service.rebuild(tenant_id="tenant-a", run_id="run-a")
        self.assertEqual(again["decisions"], ["use source A"])
        self.assertEqual(again["snapshot_revision"], 2)

    async def test_reference_window_pins_and_deduplicates_latest(self) -> None:
        service = RunContextSnapshotService(
            tenant_id="tenant-a", store=InMemoryRunContextSnapshotStore(),
            reference_window_size=3,
        )
        await service.capture(
            tenant_id="tenant-a", run_id="run-a",
            references=[
                {"ref_id": "policy", "pinned": True},
                {"ref_id": "doc", "title": "old"},
            ],
        )
        await service.capture(
            tenant_id="tenant-a", run_id="run-a",
            references=[
                {"ref_id": "policy", "title": "refreshed without pin flag"},
                {"ref_id": "doc", "title": "updated"},
                {"ref_id": "recent"},
            ],
        )
        rebuilt = await service.rebuild(tenant_id="tenant-a", run_id="run-a")
        self.assertEqual(len(rebuilt["references"]), 3)
        self.assertEqual(rebuilt["references"][0]["ref_id"], "policy")
        self.assertIs(rebuilt["references"][0]["pinned"], True)
        self.assertEqual(rebuilt["references"][1]["title"], "updated")
        with self.assertRaisesRegex(ValueError, "pinned context references"):
            await service.capture(
                tenant_id="tenant-a", run_id="run-a",
                references=[
                    {"ref_id": "p2", "pinned": True},
                    {"ref_id": "p3", "pinned": True},
                    {"ref_id": "p4", "pinned": True},
                ],
            )

    async def test_context_snapshot_rejects_cross_tenant_and_missing_recovery(self) -> None:
        service = RunContextSnapshotService(
            tenant_id="tenant-a", store=InMemoryRunContextSnapshotStore(),
        )
        with self.assertRaises(PermissionError):
            await service.capture(
                tenant_id="tenant-b", run_id="run-a", initial_context={"prompt": "x"},
            )
        with self.assertRaisesRegex(KeyError, "snapshot is missing"):
            await service.rebuild(tenant_id="tenant-a", run_id="missing")


    async def test_acceptance_restores_all_governance_fields_and_traceability(self) -> None:
        store = InMemoryRunContextSnapshotStore()
        service = RunContextSnapshotService(
            tenant_id="tenant-a", store=store, reference_window_size=8,
        )
        first = await service.ensure_initial(
            tenant_id="tenant-a",
            run_id="run-governance",
            initial_context={
                "objective": "deliver a reviewed report",
                "constraints": ["tenant-only data", "cite every factual claim"],
                "decisions": ["use approved source set"],
                "evidence": [{"evidence_id": "ev-001", "ref_id": "doc-001"}],
                "pending_items": ["obtain reviewer approval"],
            },
        )
        second = await service.capture(
            tenant_id="tenant-a",
            run_id="run-governance",
            updates={
                "decisions": ["use approved source set", "exclude stale source"],
                "evidence": [
                    {"evidence_id": "ev-001", "ref_id": "doc-001"},
                    {"evidence_id": "ev-002", "ref_id": "doc-002"},
                ],
                "pending_items": ["obtain reviewer approval", "publish final report"],
            },
            references=[
                {"ref_id": "doc-001", "type": "evidence", "pinned": True},
                {"ref_id": "doc-002", "type": "evidence"},
            ],
        )

        # Simulate a fresh service instance after worker/process restart.
        restarted = RunContextSnapshotService(
            tenant_id="tenant-a", store=store, reference_window_size=8,
        )
        restored = await restarted.rebuild(
            tenant_id="tenant-a", run_id="run-governance",
        )
        self.assertEqual(restored["objective"], "deliver a reviewed report")
        self.assertEqual(
            restored["constraints"],
            ["tenant-only data", "cite every factual claim"],
        )
        self.assertEqual(
            restored["decisions"],
            ["use approved source set", "exclude stale source"],
        )
        self.assertEqual(
            restored["evidence"],
            [
                {"evidence_id": "ev-001", "ref_id": "doc-001"},
                {"evidence_id": "ev-002", "ref_id": "doc-002"},
            ],
        )
        self.assertEqual(
            restored["pending_items"],
            ["obtain reviewer approval", "publish final report"],
        )
        self.assertEqual(
            [item["ref_id"] for item in restored["references"]],
            ["doc-001", "doc-002"],
        )
        self.assertEqual(restored["snapshot_revision"], 2)
        self.assertEqual(restored["snapshot_id"], second.snapshot_id)
        self.assertEqual(restored["snapshot_digest"], second.digest)
        self.assertNotEqual(first.snapshot_id, second.snapshot_id)

        # Recovered values are detached from the persisted revision.
        restored["constraints"].append("mutated after restore")
        restored["evidence"].clear()
        restored_again = await restarted.rebuild(
            tenant_id="tenant-a", run_id="run-governance",
        )
        self.assertEqual(len(restored_again["constraints"]), 2)
        self.assertEqual(len(restored_again["evidence"]), 2)


if __name__ == "__main__":
    unittest.main()
