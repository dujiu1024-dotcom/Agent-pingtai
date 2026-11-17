from __future__ import annotations

import unittest
from types import SimpleNamespace


class BatchWorkbenchApiAcceptanceTests(unittest.TestCase):
    def setUp(self):
        try:
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
            from agent_platform.api.batch import create_batch_router
        except ImportError:
            self.skipTest("FastAPI is required for the platform API test suite")

        class Service:
            tenant_id = "tenant-a"

            def __init__(self):
                self.records = {
                    "batch-1": {"batch_id": "batch-1", "status": "queued"},
                }
                self.calls = []

            async def submit_batch(self, **kwargs):
                self.calls.append(("submit", kwargs))
                result = {"batch_id": "batch-2", "status": "queued"}
                self.records["batch-2"] = result
                return result

            async def list_batches(self, **kwargs):
                self.calls.append(("list", kwargs))
                return list(self.records.values())

            async def get_batch(self, **kwargs):
                self.calls.append(("get", kwargs))
                return self.records.get(kwargs["batch_id"])

            async def cancel_batch(self, **kwargs):
                self.calls.append(("cancel", kwargs))
                result = self.records.get(kwargs["batch_id"])
                if result is None:
                    return None
                result = {**result, "status": "cancelled"}
                self.records[kwargs["batch_id"]] = result
                return result

            async def retry_batch(self, **kwargs):
                self.calls.append(("retry", kwargs))
                if kwargs["batch_id"] not in self.records:
                    return None
                result = {
                    "batch_id": "batch-retry-1",
                    "retry_of": kwargs["batch_id"],
                    "status": "queued",
                }
                self.records[result["batch_id"]] = result
                return result

        self.service = Service()

        async def resolve_principal(_request):
            return SimpleNamespace(tenant_id="tenant-a", user_id="user-a")

        async def resolve_service(_principal):
            return self.service

        async def authorize(_principal, resource, action):
            return resource != "denied" and action in {
                "read", "create", "execute", "cancel", "retry",
            }

        app = FastAPI()
        app.include_router(create_batch_router(
            resolve_principal=resolve_principal,
            resolve_service=resolve_service,
            authorize=authorize,
        ))
        self.client = TestClient(app)

    def test_submit_list_get_cancel_retry_workbench_flow(self):
        submitted = self.client.post(
            "/api/platform/v1/workbench/batches",
            json={
                "template_id": "agent-template",
                "dataset": [{"prompt": "first"}, {"prompt": "second"}],
                "max_concurrency": 2,
                "idempotency_key": "submit-001",
            },
        )
        self.assertEqual(submitted.status_code, 202)
        self.assertEqual(submitted.json()["batch_id"], "batch-2")
        submit_call = next(call for call in self.service.calls if call[0] == "submit")[1]
        self.assertEqual(submit_call["tenant_id"], "tenant-a")
        self.assertEqual(submit_call["actor_id"], "user-a")
        self.assertEqual(submit_call["template_id"], "agent-template")
        self.assertEqual(len(submit_call["dataset"]), 2)

        listed = self.client.get("/api/platform/v1/workbench/batches")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual({item["batch_id"] for item in listed.json()["items"]}, {"batch-1", "batch-2"})

        queried = self.client.get("/api/platform/v1/workbench/batches/batch-1")
        self.assertEqual(queried.status_code, 200)
        self.assertEqual(queried.json()["status"], "queued")

        cancelled = self.client.post("/api/platform/v1/workbench/batches/batch-1/cancel")
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["status"], "cancelled")

        retried = self.client.post(
            "/api/platform/v1/workbench/batches/batch-1/retry",
            json={"idempotency_key": "retry-001"},
        )
        self.assertEqual(retried.status_code, 202)
        self.assertEqual(retried.json()["retry_of"], "batch-1")
        retry_call = next(call for call in self.service.calls if call[0] == "retry")[1]
        self.assertEqual(retry_call["idempotency_key"], "retry-001")

    def test_authorization_tenant_mismatch_and_missing_batch_fail_closed(self):
        denied = self.client.get("/api/platform/v1/workbench/batches/denied")
        self.assertEqual(denied.status_code, 403)

        missing = self.client.get("/api/platform/v1/workbench/batches/missing")
        self.assertEqual(missing.status_code, 404)

        missing_cancel = self.client.post(
            "/api/platform/v1/workbench/batches/missing/cancel"
        )
        self.assertEqual(missing_cancel.status_code, 404)

        class ForeignTenantService:
            tenant_id = "tenant-b"

        async def foreign_service(_principal):
            return ForeignTenantService()

        from fastapi import FastAPI
        from agent_platform.api.batch import create_batch_router

        async def principal(_request):
            return SimpleNamespace(tenant_id="tenant-a", user_id="user-a")

        async def allow(_principal, _resource, _action):
            return True

        app = FastAPI()
        app.include_router(create_batch_router(
            resolve_principal=principal,
            resolve_service=foreign_service,
            authorize=allow,
        ))
        from fastapi.testclient import TestClient
        foreign_client = TestClient(app)
        response = foreign_client.get("/api/platform/v1/workbench/batches")
        self.assertEqual(response.status_code, 403)

    def test_invalid_submit_and_retry_requests_are_rejected_before_service_call(self):
        invalid_dataset = self.client.post(
            "/api/platform/v1/workbench/batches",
            json={"template_id": "template", "dataset": []},
        )
        self.assertEqual(invalid_dataset.status_code, 422)

        bool_concurrency = self.client.post(
            "/api/platform/v1/workbench/batches",
            json={
                "template_id": "template",
                "dataset": [{"input": "x"}],
                "max_concurrency": True,
            },
        )
        self.assertEqual(bool_concurrency.status_code, 422)

        missing_retry_key = self.client.post(
            "/api/platform/v1/workbench/batches/batch-1/retry",
            json={},
        )
        self.assertEqual(missing_retry_key.status_code, 422)
        self.assertFalse(any(call[0] == "submit" for call in self.service.calls))
        self.assertFalse(any(call[0] == "retry" for call in self.service.calls))


if __name__ == "__main__":
    unittest.main()
