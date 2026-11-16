"""Tests for durable Run context snapshot assembly and recovery reconstruction."""
import asyncio

import pytest

from agent_platform.runtime.run_context import (
    InMemoryRunContextSnapshotStore,
    RunContextSnapshotService,
)


def test_context_snapshot_rebuilds_latest_revision_and_is_defensive() -> None:
    async def scenario() -> None:
        store = InMemoryRunContextSnapshotStore()
        service = RunContextSnapshotService(
            tenant_id="tenant-a", store=store, reference_window_size=3,
        )
        first = await service.ensure_initial(
            tenant_id="tenant-a", run_id="run-a",
            initial_context={"prompt": "summarize", "objective": "summary"},
        )
        assert first.revision == 1
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
        assert rebuilt["prompt"] == "summarize"
        assert rebuilt["objective"] == "summary"
        assert rebuilt["decisions"] == ["use source A"]
        assert [ref["ref_id"] for ref in rebuilt["references"]] == [
            "pinned", "new-1", "new-2",
        ]
        rebuilt["decisions"].append("mutated outside store")
        again = await service.rebuild(tenant_id="tenant-a", run_id="run-a")
        assert again["decisions"] == ["use source A"]
        assert again["snapshot_revision"] == 2

    asyncio.run(scenario())


def test_context_snapshot_keeps_pinned_references_and_deduplicates_latest() -> None:
    async def scenario() -> None:
        service = RunContextSnapshotService(
            tenant_id="tenant-a", store=InMemoryRunContextSnapshotStore(),
            reference_window_size=2,
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
                {"ref_id": "doc", "title": "updated"},
                {"ref_id": "recent"},
            ],
        )
        rebuilt = await service.rebuild(tenant_id="tenant-a", run_id="run-a")
        assert len(rebuilt["references"]) == 2
        assert rebuilt["references"][0]["ref_id"] == "policy"
        assert rebuilt["references"][1]["title"] == "updated"
        with pytest.raises(ValueError, match="pinned context references"):
            await service.capture(
                tenant_id="tenant-a", run_id="run-a",
                references=[
                    {"ref_id": "p2", "pinned": True},
                    {"ref_id": "p3", "pinned": True},
                ],
            )

    asyncio.run(scenario())


def test_context_snapshot_rejects_cross_tenant_and_missing_recovery() -> None:
    async def scenario() -> None:
        service = RunContextSnapshotService(
            tenant_id="tenant-a", store=InMemoryRunContextSnapshotStore(),
        )
        with pytest.raises(PermissionError):
            await service.capture(
                tenant_id="tenant-b", run_id="run-a", initial_context={"prompt": "x"},
            )
        with pytest.raises(KeyError, match="snapshot is missing"):
            await service.rebuild(tenant_id="tenant-a", run_id="missing")

    asyncio.run(scenario())
