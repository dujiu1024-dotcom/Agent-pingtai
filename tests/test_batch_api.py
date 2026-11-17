from __future__ import annotations

import unittest
from types import SimpleNamespace


class BatchApiTests(unittest.TestCase):
    def test_submit_query_cancel_retry_and_tenant_authorization(self):
        try:
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
            from agent_platform.api.batch import create_batch_router
        except ImportError:
            self.skipTest("FastAPI is installed in the platform API test environment")

        class Service:
            tenant_id = "tenant-a"

            async def submit_batch(self, **kwargs):
                self.submitted = kwargs
                return {"batch_id": "batch-1", "status": "queued"}

            async def list_batches(self, **kwargs):
                return [{"batch_id": "batch-1", "status": "running"}]

            async def get_batch(self, **kwargs):
                return {"batch_id": kwargs["batch_id"], "status": "running"}

            async def cancel_batch(self, **kwargs):
                return {"batch_id": kwargs["batch_id"], "status": "cancelled"}

            async def retry_batch(self, **kwargs):
                self.retried = kwargs
                return {"batch_id": "batch-2", "retry_of": kwargs["batch_id"], "status": "queued"}

        service = Service()
        async def principal(_request):
            return SimpleNamespace(tenant_id="tenant-a", user_id="user-a")

        async def resolve(_principal):
            return service

        async def authorize(_principal, resource, action):
            return resource != "forbidden" and action in {
                "read", "create", "execute", "cancel", "retry",
            }

        app = FastAPI()
        app.include_router(create_batch_router(
            resolve_principal=principal, resolve_service=resolve, authorize=authorize,
        ))
        client = TestClient(app)

        submitted = client.post("/api/platform/v1/workbench/batches", json={
            "template_id": "agent-template", "dataset": [{"prompt": "one"}, {"prompt": "two"}],
            "max_concurrency": 2, "idempotency_key": "submit-1",
        })
        self.assertEqual(submitted.status_code, 202)
        self.assertEqual(submitted.json()["batch_id"], "batch-1")
        self.assertEqual(service.submitted["tenant_id"], "tenant-a")
        self.assertEqual(service.submitted["max_concurrency"], 2)

        listed = client.get("/api/platform/v1/workbench/batches")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["items"][0]["batch_id"], "batch-1")
        queried = client.get("/api/platform/v1/workbench/batches/batch-1")
        self.assertEqual(queried.status_code, 200)
        self.assertEqual(queried.json()["status"], "running")
        cancelled = client.post("/api/platform/v1/workbench/batches/batch-1/cancel")
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["status"], "cancelled")
        retried = client.post(
            "/api/platform/v1/workbench/batches/batch-1/retry",
            json={"idempotency_key": "retry-1"},
        )
        self.assertEqual(retried.status_code, 202)
        self.assertEqual(retried.json()["retry_of"], "batch-1")
        self.assertEqual(service.retried["idempotency_key"], "retry-1")

    def test_validation_authorization_and_service_tenant_boundary(self):
        try:
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
            from agent_platform.api.batch import create_batch_router
        except ImportError:
            self.skipTest("FastAPI is installed in the platform API test environment")

        class Service:
            tenant_id = "tenant-b"

        async def principal(_request):
            return SimpleNamespace(tenant_id="tenant-a", user_id="user-a")

        async def resolve(_principal):
            return Service()

        async def authorize(_principal, _resource, _action):
            return True

        app = FastAPI()
        app.include_router(create_batch_router(
            resolve_principal=principal, resolve_service=resolve, authorize=authorize,
        ))
        client = TestClient(app)
        mismatch = client.get("/api/platform/v1/workbench/batches")
        self.assertEqual(mismatch.status_code, 403)

        class MatchingService:
            tenant_id = "tenant-a"
        async def resolve_matching(_principal):
            return MatchingService()
        app = FastAPI()
        app.include_router(create_batch_router(
            resolve_principal=principal, resolve_service=resolve_matching,
            authorize=authorize,
        ))
        client = TestClient(app)
        invalid = client.post("/api/platform/v1/workbench/batches", json={
            "template_id": "template", "dataset": [], "max_concurrency": 0,
        })
        self.assertEqual(invalid.status_code, 422)
        denied = client.get("/api/platform/v1/workbench/batches/forbidden")
        self.assertEqual(denied.status_code, 403)


if __name__ == "__main__":
    unittest.main()
