from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from agent_platform.knowledge.acl_invalidation_replay import (
    FileCheckpointStore,
    KnowledgeInvalidationReplay,
    MemoryCheckpointStore,
)


class FakeInvalidationSource:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events
        self.watermark_calls = 0

    async def high_watermark(self) -> int:
        self.watermark_calls += 1
        return max((event["event_id"] for event in self.events), default=0)

    async def list_after(self, event_id: int, *, limit: int):
        return [
            event for event in self.events
            if event["event_id"] > event_id
        ][:limit]


class KnowledgeInvalidationReplayTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_connect_rebuilds_snapshot_then_replays_newer_events(self):
        source = FakeInvalidationSource([
            {"event_id": 1, "resource_id": "a"},
            {"event_id": 2, "resource_id": "b"},
            {"event_id": 3, "resource_id": "c"},
        ])
        checkpoints = MemoryCheckpointStore()
        applied: list[int] = []
        rebuilt: list[int] = []

        async def apply(event):
            applied.append(event["event_id"])

        async def rebuild(high_watermark):
            rebuilt.append(high_watermark)
            source.events.append({"event_id": 4, "resource_id": "d"})

        replay = KnowledgeInvalidationReplay(
            subscriber_id="cache-a", source=source, checkpoints=checkpoints,
            apply=apply, rebuild=rebuild,
        )
        self.assertEqual(await replay.reconnect(), 1)
        self.assertEqual(rebuilt, [3])
        self.assertEqual(applied, [4])
        self.assertEqual(await checkpoints.load("cache-a"), 4)

    async def test_checkpoint_is_per_subscriber_and_monotonic(self):
        store = MemoryCheckpointStore()
        await store.advance("cache-a", 12)
        await store.advance("cache-a", 7)
        await store.advance("cache-b", 3)
        self.assertEqual(await store.load("cache-a"), 12)
        self.assertEqual(await store.load("cache-b"), 3)

    async def test_failed_apply_does_not_advance_offset_and_is_retried(self):
        source = FakeInvalidationSource([
            {"event_id": 5, "resource_id": "restricted-doc"},
        ])
        store = MemoryCheckpointStore()
        calls = 0

        async def apply(_event):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("cache temporarily unavailable")

        async def rebuild(_watermark):
            return None

        replay = KnowledgeInvalidationReplay(
            subscriber_id="cache-a", source=source, checkpoints=store,
            apply=apply, rebuild=rebuild,
        )
        with self.assertRaisesRegex(RuntimeError, "temporarily unavailable"):
            await replay.reconnect()
        self.assertEqual(await store.load("cache-a"), 0)
        self.assertEqual(await replay.reconnect(), 1)
        self.assertEqual(await store.load("cache-a"), 5)

    async def test_reconnect_uses_checkpoint_without_unnecessary_full_rebuild(self):
        source = FakeInvalidationSource([
            {"event_id": 1}, {"event_id": 2}, {"event_id": 3},
        ])
        store = MemoryCheckpointStore()
        await store.advance("cache-a", 2)
        applied: list[int] = []
        rebuilt: list[int] = []

        async def apply(event):
            applied.append(event["event_id"])

        async def rebuild(watermark):
            rebuilt.append(watermark)

        replay = KnowledgeInvalidationReplay(
            subscriber_id="cache-a", source=source, checkpoints=store,
            apply=apply, rebuild=rebuild,
        )
        self.assertEqual(await replay.reconnect(), 1)
        self.assertEqual(applied, [3])
        self.assertEqual(rebuilt, [])
        self.assertEqual(source.watermark_calls, 0)

    async def test_file_checkpoint_survives_store_recreation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoints.json"
            first = FileCheckpointStore(path)
            await first.advance("cache-a", 9)
            await first.advance("cache-a", 4)
            await first.advance("cache-b", 2)

            restarted = FileCheckpointStore(path)
            self.assertEqual(await restarted.load("cache-a"), 9)
            self.assertEqual(await restarted.load("cache-b"), 2)


if __name__ == "__main__":
    unittest.main()
