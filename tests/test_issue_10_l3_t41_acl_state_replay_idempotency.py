from __future__ import annotations

import unittest
from typing import Any, Mapping, Sequence

from agent_platform.knowledge.acl_invalidation_replay import (
    KnowledgeInvalidationReplay,
    MemoryCheckpointStore,
)


class MemoryInvalidationLog:
    """Durable-log-shaped test source with a stable snapshot watermark."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = list(events)

    async def high_watermark(self) -> int:
        return max((int(event["event_id"]) for event in self.events), default=0)

    async def list_after(
        self, event_id: int, *, limit: int,
    ) -> Sequence[Mapping[str, Any]]:
        return [
            event for event in self.events
            if int(event["event_id"]) > event_id
        ][:limit]


class FailOnceCheckpointStore:
    """Simulate a crash after cache mutation but before offset persistence."""

    def __init__(self) -> None:
        self.inner = MemoryCheckpointStore()
        self.fail_event_id: int | None = None

    async def load(self, subscriber_id: str) -> int | None:
        return await self.inner.load(subscriber_id)

    async def advance(self, subscriber_id: str, event_id: int) -> None:
        if self.fail_event_id == event_id:
            self.fail_event_id = None
            raise RuntimeError("simulated checkpoint write failure")
        await self.inner.advance(subscriber_id, event_id)


class MemoryACLState:
    """Materialized ACL cache with replace-snapshot and idempotent invalidation."""

    def __init__(self) -> None:
        self.grants: dict[str, frozenset[str]] = {}
        self.applied_event_ids: set[int] = set()
        self.invalidation_effects = 0

    async def replace_from_authoritative_snapshot(
        self, snapshot: Mapping[str, Sequence[str]],
    ) -> None:
        # Replacement, not merge: stale grants absent from the source disappear.
        self.grants = {
            resource_id: frozenset(principals)
            for resource_id, principals in snapshot.items()
        }

    async def apply_invalidation(self, event: Mapping[str, Any]) -> None:
        event_id = int(event["event_id"])
        if event_id in self.applied_event_ids:
            return
        resource_id = str(event["resource_id"])
        self.grants.pop(resource_id, None)
        self.applied_event_ids.add(event_id)
        self.invalidation_effects += 1


class ACLStateReplayTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_replay_rebuilds_complete_acl_snapshot_then_catches_up(self) -> None:
        source = MemoryInvalidationLog([
            {"event_id": 1, "resource_type": "document", "resource_id": "doc-old"},
            {"event_id": 2, "resource_type": "chunk", "resource_id": "chunk-old"},
        ])
        cache = MemoryACLState()
        checkpoints = MemoryCheckpointStore()
        authoritative = {
            "doc-current": ["user:owner", "role:reader"],
            "chunk-current": ["project:alpha"],
        }
        rebuilt_at: list[int] = []

        async def rebuild(watermark: int) -> None:
            rebuilt_at.append(watermark)
            await cache.replace_from_authoritative_snapshot(authoritative)
            # A concurrent ACL change after the snapshot watermark must be replayed.
            source.events.append({
                "event_id": 3,
                "resource_type": "document",
                "resource_id": "doc-current",
                "reason": "acl_changed",
            })

        replay = KnowledgeInvalidationReplay(
            subscriber_id="knowledge-acl-cache-a",
            source=source,
            checkpoints=checkpoints,
            apply=cache.apply_invalidation,
            rebuild=rebuild,
        )

        self.assertEqual(await replay.reconnect(), 1)
        self.assertEqual(rebuilt_at, [2])
        self.assertEqual(cache.grants, {
            "chunk-current": frozenset({"project:alpha"}),
        })
        self.assertEqual(await checkpoints.load("knowledge-acl-cache-a"), 3)

        # Reconnect at the persisted consumer offset is a no-op.
        self.assertEqual(await replay.reconnect(), 0)
        self.assertEqual(cache.invalidation_effects, 1)

    async def test_duplicate_delivery_after_checkpoint_failure_is_idempotent(self) -> None:
        source = MemoryInvalidationLog([
            {"event_id": 1, "resource_type": "document", "resource_id": "doc-a"},
        ])
        cache = MemoryACLState()
        cache.grants = {"doc-a": frozenset({"user:alice"})}
        checkpoints = FailOnceCheckpointStore()

        async def rebuild(watermark: int) -> None:
            self.assertEqual(watermark, 1)
            await cache.replace_from_authoritative_snapshot({
                "doc-a": ["user:alice"],
            })
            source.events.append({
                "event_id": 2,
                "resource_type": "document",
                "resource_id": "doc-a",
                "reason": "acl_changed",
            })

        replay = KnowledgeInvalidationReplay(
            subscriber_id="knowledge-acl-cache-a",
            source=source,
            checkpoints=checkpoints,
            apply=cache.apply_invalidation,
            rebuild=rebuild,
        )

        # Initial rebuild checkpoint succeeds; applying event 2 succeeds, but
        # persisting offset 2 fails, so event 2 is deliberately delivered again.
        checkpoints.fail_event_id = 2
        with self.assertRaisesRegex(RuntimeError, "checkpoint write failure"):
            await replay.reconnect()
        self.assertEqual(cache.grants, {})
        self.assertEqual(cache.invalidation_effects, 1)
        self.assertEqual(await checkpoints.load("knowledge-acl-cache-a"), 1)

        self.assertEqual(await replay.reconnect(), 1)
        self.assertEqual(cache.grants, {})
        self.assertEqual(cache.invalidation_effects, 1)
        self.assertEqual(await checkpoints.load("knowledge-acl-cache-a"), 2)

    async def test_forced_rebuild_replaces_stale_acl_state_and_is_repeatable(self) -> None:
        source = MemoryInvalidationLog([])
        cache = MemoryACLState()
        cache.grants = {
            "stale-doc": frozenset({"user:removed"}),
            "current-doc": frozenset({"user:old"}),
        }
        snapshot = {"current-doc": ["user:current"]}
        rebuilt: list[int] = []

        async def rebuild(watermark: int) -> None:
            rebuilt.append(watermark)
            await cache.replace_from_authoritative_snapshot(snapshot)

        replay = KnowledgeInvalidationReplay(
            subscriber_id="knowledge-acl-cache-a",
            source=source,
            checkpoints=MemoryCheckpointStore(),
            apply=cache.apply_invalidation,
            rebuild=rebuild,
        )

        await replay.reconnect(force_full=True)
        await replay.reconnect(force_full=True)
        self.assertEqual(rebuilt, [0, 0])
        self.assertEqual(cache.grants, {
            "current-doc": frozenset({"user:current"}),
        })


if __name__ == "__main__":
    unittest.main()
