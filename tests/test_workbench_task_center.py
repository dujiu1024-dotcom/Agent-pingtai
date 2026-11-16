from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.workbench import create_workbench_router
from agent_platform.identity.models import Principal


class FakeTaskService:
    def __init__(self):
        self.items = {}

    async def list_tasks(self, *, tenant_id, actor_id, limit=100):
        return list(self.items.values())[:limit]

    async def submit_task(
        self, *, tenant_id, actor_id, agent_id, project_id, session_id,
        input, idempotency_key=None,
    ):
        task = {
            "task_id": "task-1", "agent_id": agent_id, "project_id": project_id,
            "session_id": session_id, "input": dict(input), "status": "queued",
        }
        self.items[task["task_id"]] = task
        return task

    async def get_task(self, *, tenant_id, task_id):
        return self.items.get(task_id)

    async def cancel_task(self, *, tenant_id, task_id, actor_id):
        task = self.items.get(task_id)
        if task is None:
            return None
        task = {**task, "status": "cancelled"}
        self.items[task_id] = task
        return task


class WorkbenchTaskCenterTests(unittest.TestCase):
    def setUp(self):
        self.service = FakeTaskService()

        async def principal(request):
            return Principal(
                user_id="user-a", tenant_id=request.headers.get("x-tenant", "tenant-a"),
                session_id="session", email="user@example.test", display_name="User",
            )

        async def resolve_service(actor):
            return self.service

        async def authorize(actor, resource_id, action):
            if action in {"create", "read"} and resource_id == "*":
                return True
            if action == "execute":
                return resource_id == "agent-a"
            return resource_id == "task-1"

        app = FastAPI()
        app.include_router(create_workbench_router(
            features={"task_execution": True},
            resolve_principal=principal,
            resolve_task_service=resolve_service,
            authorize_task=authorize,
        ))
        self.client = TestClient(app)

    def test_task_lifecycle_is_connected_to_workbench(self):
        caps = self.client.get("/api/platform/v1/workbench/capabilities")
        self.assertTrue(caps.json()["features"]["task_execution"])

        submitted = self.client.post(
            "/api/platform/v1/workbench/tasks",
            json={"agent_id": "agent-a", "input": {"text": "analyze"}},
        )
        self.assertEqual(submitted.status_code, 202)
        self.assertEqual(submitted.json()["task_id"], "task-1")

        listed = self.client.get("/api/platform/v1/workbench/tasks")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["items"][0]["status"], "queued")

        detail = self.client.get("/api/platform/v1/workbench/tasks/task-1")
        self.assertEqual(detail.status_code, 200)

        cancelled = self.client.post("/api/platform/v1/workbench/tasks/task-1/cancel")
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["status"], "cancelled")

    def test_task_submission_rejects_missing_agent_and_input(self):
        response = self.client.post(
            "/api/platform/v1/workbench/tasks",
            json={"agent_id": "agent-a", "input": {}},
        )
        self.assertEqual(response.status_code, 400)

    def test_tenant_bound_task_service_is_enforced(self):
        class ForeignService(FakeTaskService):
            class Repository:
                tenant_id = "tenant-b"

            repository = Repository()

        async def foreign_service(actor):
            return ForeignService()

        async def principal(request):
            return Principal(
                user_id="user-a", tenant_id="tenant-a", session_id="session",
                email="user@example.test", display_name="User",
            )

        async def authorize(actor, resource_id, action):
            return True

        app = FastAPI()
        app.include_router(create_workbench_router(
            features={"task_execution": True},
            resolve_principal=principal,
            resolve_task_service=foreign_service,
            authorize_task=authorize,
        ))
        response = TestClient(app).get("/api/platform/v1/workbench/tasks")
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
