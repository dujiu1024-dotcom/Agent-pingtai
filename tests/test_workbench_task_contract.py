from __future__ import annotations

import unittest
from types import SimpleNamespace


class WorkbenchTaskContractTests(unittest.TestCase):
    def setUp(self):
        try:
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
            from agent_platform.api.workbench import create_workbench_router
        except ImportError:
            self.skipTest("FastAPI is required for workbench API contract tests")

        self.principal = SimpleNamespace(tenant_id="tenant-a", user_id="user-a")
        self.authorizer_calls = []

        async def resolve_principal(_request):
            return self.principal

        async def resolve_service(_principal):
            return self.service

        async def authorize(principal, resource_id, action):
            self.authorizer_calls.append((principal.tenant_id, resource_id, action))
            return True

        app = FastAPI()
        app.include_router(create_workbench_router(
            features={"task_execution": True},
            resolve_principal=resolve_principal,
            resolve_task_service=resolve_service,
            authorize_task=authorize,
        ))
        self.client = TestClient(app)

    def _service(self, *, tenant_id="tenant-a"):
        class Service:
            def __init__(self, owner):
                self.tenant_id = owner
                self.submissions = []
                self.items = [{"task_id": "task-1", "status": "queued"}]

            async def list_tasks(self, **kwargs):
                self.list_kwargs = kwargs
                return self.items

            async def submit_task(self, **kwargs):
                self.submissions.append(kwargs)
                return {"task_id": "task-2", "status": "queued"}

            async def get_task(self, **kwargs):
                return {"task_id": kwargs["task_id"], "status": "running"}

            async def cancel_task(self, **kwargs):
                return {"task_id": kwargs["task_id"], "status": "cancelled"}

            async def retry_task(self, **kwargs):
                return {"task_id": "task-retry", "status": "queued",
                        "retry_of_run_id": kwargs["task_id"]}

        return Service(tenant_id)

    def test_list_tasks_enforces_limit_and_tenant_scoping(self):
        self.service = self._service()
        response = self.client.get("/api/platform/v1/workbench/tasks?limit=201")
        self.assertEqual(response.status_code, 400)
        response = self.client.get("/api/platform/v1/workbench/tasks?limit=25")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"], self.service.items)
        self.assertEqual(self.service.list_kwargs, {
            "tenant_id": "tenant-a", "actor_id": "user-a", "limit": 25,
        })

    def test_submit_task_passes_tenant_actor_and_idempotency_key(self):
        self.service = self._service()
        response = self.client.post(
            "/api/platform/v1/workbench/tasks",
            json={
                "agent_id": "agent-1", "project_id": "project-1",
                "session_id": "session-1", "idempotency_key": "submit-req-1",
                "input": {"prompt": "summarize"},
            },
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["task_id"], "task-2")
        self.assertEqual(self.service.submissions, [{
            "tenant_id": "tenant-a", "actor_id": "user-a",
            "agent_id": "agent-1", "project_id": "project-1",
            "session_id": "session-1", "input": {"prompt": "summarize"},
            "idempotency_key": "submit-req-1", "priority": 100,
        }])
        self.assertIn(("tenant-a", "agent-1", "execute"), self.authorizer_calls)
        self.assertIn(("tenant-a", "project-1", "use_project"), self.authorizer_calls)
        self.assertIn(("tenant-a", "session-1", "use_session"), self.authorizer_calls)

    def test_submit_task_rejects_missing_agent_or_empty_input(self):
        self.service = self._service()
        for body in (
            {"input": {"prompt": "x"}},
            {"agent_id": "agent-1", "input": {}},
        ):
            with self.subTest(body=body):
                response = self.client.post(
                    "/api/platform/v1/workbench/tasks", json=body,
                )
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.service.submissions, [])

    def test_custom_priority_is_forwarded_to_task_service(self):
        self.service = self._service()
        response = self.client.post(
            "/api/platform/v1/workbench/tasks",
            json={"agent_id": "agent-1", "input": {"prompt": "x"}, "priority": 10},
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.service.submissions[0]["priority"], 10)

    def test_retry_task_requires_idempotency_key_and_forwards_request(self):
        self.service = self._service()
        response = self.client.post(
            "/api/platform/v1/workbench/tasks/task-1/retry",
            json={"idempotency_key": "retry-1"},
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["retry_of_run_id"], "task-1")

        invalid = self.client.post(
            "/api/platform/v1/workbench/tasks/task-1/retry", json={},
        )
        self.assertEqual(invalid.status_code, 422)

    def test_task_service_tenant_mismatch_is_denied(self):
        self.service = self._service(tenant_id="tenant-b")
        response = self.client.get("/api/platform/v1/workbench/tasks")
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(getattr(self.service, "list_kwargs", None))


if __name__ == "__main__":
    unittest.main()
