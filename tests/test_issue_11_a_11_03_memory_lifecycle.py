from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.knowledge_memory import create_knowledge_memory_router
from agent_platform.knowledge.repository import InMemoryKnowledgeRepository
from agent_platform.knowledge.service import KnowledgeService
from agent_platform.memory.service import (
    InMemoryMemoryRepository,
    MemoryPrincipal,
    MemoryService,
)


class MemoryLifecycleAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.cleanup_attempts = []
        self.fail_cleanup_memory_id = None

        async def cleanup(tenant_id: str, memory_id: str) -> None:
            self.cleanup_attempts.append((tenant_id, memory_id))
            attempts_for_memory = sum(1 for _, item_id in self.cleanup_attempts if item_id == memory_id)
            if memory_id == self.fail_cleanup_memory_id and attempts_for_memory == 1:
                raise RuntimeError("derived index temporarily unavailable")

        self.memory = MemoryService(InMemoryMemoryRepository(), on_delete=cleanup)

        async def resolve_memory(request):
            return MemoryPrincipal(
                tenant_id=request.headers.get("x-tenant", "tenant-a"),
                user_id=request.headers.get("x-user", "user-a"),
            )

        app = FastAPI()
        app.include_router(
            create_knowledge_memory_router(
                knowledge=KnowledgeService(InMemoryKnowledgeRepository()),
                memory=self.memory,
                resolve_knowledge_principal=resolve_memory,
                resolve_memory_principal=resolve_memory,
            )
        )
        self.client = TestClient(app, raise_server_exceptions=False)

    def _create_and_confirm(self, *, expires_at: str | None = None) -> str:
        payload = {
            "scope": "user",
            "content": "prefers concise status reports",
            "source_ref": "conversation:turn-100",
            "confidence": 0.82,
        }
        if expires_at is not None:
            payload["expires_at"] = expires_at
        created = self.client.post("/api/platform/v1/memories/candidates", json=payload)
        self.assertEqual(created.status_code, 200, created.text)
        memory_id = created.json()["memory_id"]
        confirmed = self.client.post(f"/api/platform/v1/memories/{memory_id}/confirm")
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        return memory_id

    def test_history_exposes_each_version_source_and_correction(self):
        memory_id = self._create_and_confirm()
        corrected = self.client.patch(
            f"/api/platform/v1/memories/{memory_id}",
            json={
                "content": "prefers concise weekly reports",
                "source_ref": "user-correction:ticket-42",
                "confidence": 0.97,
            },
        )
        self.assertEqual(corrected.status_code, 200, corrected.text)
        self.assertEqual(corrected.json()["version"], 3)

        history = self.client.get(f"/api/platform/v1/memories/{memory_id}/history")
        self.assertEqual(history.status_code, 200, history.text)
        versions = history.json()["items"]
        self.assertEqual([item["version"] for item in versions], [1, 2, 3])
        self.assertEqual(
            [item["source_ref"] for item in versions],
            ["conversation:turn-100", "conversation:turn-100", "user-correction:ticket-42"],
        )
        self.assertEqual(versions[0]["created_by"], "user-a")
        self.assertEqual(versions[2]["content"], "prefers concise weekly reports")
        self.assertEqual(versions[2]["confidence"], 0.97)
        self.assertTrue(all(item["recorded_at"] for item in versions))

    def test_expiry_removes_memory_and_delete_cleanup_can_be_retried(self):
        expired_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        expired_id = self._create_and_confirm(expires_at=expired_at)
        expired_count = self.memory.expire_due(
            tenant_id="tenant-a", now=datetime.now(timezone.utc),
        )
        self.assertEqual(awaitable_result(expired_count), 1)
        self.assertEqual(
            self.client.get("/api/platform/v1/memories").json()["items"], [],
        )
        expired_history = self.client.get(
            f"/api/platform/v1/memories/{expired_id}/history"
        ).json()["items"]
        self.assertEqual(expired_history[-1]["status"], "expired")

        deletable_id = self._create_and_confirm()
        self.fail_cleanup_memory_id = deletable_id
        first = self.client.delete(f"/api/platform/v1/memories/{deletable_id}")
        self.assertEqual(first.status_code, 500)
        # The tombstone is durable even when derived-data cleanup fails.
        self.assertEqual(
            (awaitable_result(self.memory.repository.get("tenant-a", deletable_id))).status.value,
            "deleted",
        )
        retry = self.client.delete(f"/api/platform/v1/memories/{deletable_id}")
        self.assertEqual(retry.status_code, 204)
        repeated = self.client.delete(f"/api/platform/v1/memories/{deletable_id}")
        self.assertEqual(repeated.status_code, 204)
        self.assertEqual(
            [item for item in self.cleanup_attempts if item[1] == deletable_id],
            [("tenant-a", deletable_id), ("tenant-a", deletable_id), ("tenant-a", deletable_id)],
        )
        self.assertEqual(self.client.get("/api/platform/v1/memories").json()["items"], [])


def awaitable_result(awaitable):
    """Run an async repository/service operation from this synchronous test case."""
    import asyncio

    return asyncio.run(awaitable)


if __name__ == "__main__":
    unittest.main()
