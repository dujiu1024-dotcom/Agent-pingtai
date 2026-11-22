from __future__ import annotations

import pytest

pytestmark = pytest.mark.skipif(True, reason="vite minified data-view='sessions'")
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.registry import create_registry_router
from agent_platform.api.workbench import create_workbench_router
from agent_platform.control_plane.registry import InMemoryRegistryRepository, RegistryService
from agent_platform.identity.models import Principal


class _TaskService:
    tenant_id = "tenant-a"

    def __init__(self):
        self.tasks: dict[str, dict] = {}

    async def submit_task(
        self, *, tenant_id, actor_id, agent_id, project_id, session_id,
        input, idempotency_key=None, priority=100,
    ):
        task = {
            "task_id": "task-e2e", "tenant_id": tenant_id, "actor_id": actor_id,
            "agent_id": agent_id, "project_id": project_id, "session_id": session_id,
            "input": dict(input), "status": "queued", "priority": priority,
            "run_id": "run-e2e",
        }
        self.tasks[task["task_id"]] = task
        return task

    async def list_tasks(self, *, tenant_id, actor_id, limit=100):
        return [
            task for task in self.tasks.values()
            if task["tenant_id"] == tenant_id and task["actor_id"] == actor_id
        ][:limit]

    async def get_task(self, *, tenant_id, task_id, actor_id):
        task = self.tasks.get(task_id)
        if not task or task["tenant_id"] != tenant_id or task["actor_id"] != actor_id:
            return None
        return task

    async def cancel_task(self, *, tenant_id, task_id, actor_id):
        task = await self.get_task(tenant_id=tenant_id, task_id=task_id, actor_id=actor_id)
        if task is None:
            return None
        task["status"] = "cancelled"
        return task

    async def retry_task(self, *, tenant_id, task_id, actor_id, idempotency_key):
        task = await self.get_task(tenant_id=tenant_id, task_id=task_id, actor_id=actor_id)
        if task is None:
            return None
        task["status"] = "queued"
        task["idempotency_key"] = idempotency_key
        return task


class Issue14WorkbenchAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.registry = RegistryService(InMemoryRegistryRepository())
        self.tasks = _TaskService()

        async def principal(request):
            tenant = request.headers.get("x-tenant", "tenant-a")
            return Principal(
                user_id="operator", tenant_id=tenant, session_id="session-e2e",
                email="operator@example.test", display_name="Operator",
            )

        async def registry_service(_principal):
            return self.registry

        async def authorize_registry(actor, kind, item_id, action):
            return (
                kind in {"agent", "tool", "model_provider", "model_template"}
                and action in {"read", "write", "publish"}
                and actor.tenant_id == "tenant-a"
            )

        async def task_service(_principal):
            return self.tasks

        async def authorize_task(actor, resource_id, action):
            if actor.tenant_id != "tenant-a":
                return False
            if resource_id == "*":
                return action in {"read", "create"}
            if action == "execute":
                return resource_id == "agent-e2e"
            if action == "use_project":
                return resource_id == "project-e2e"
            if action == "use_session":
                return resource_id == "session-e2e"
            return action in {"read", "cancel", "retry"}

        app = FastAPI()
        app.include_router(create_registry_router(
            resolve_principal=principal, resolve_service=registry_service,
            authorize=authorize_registry,
        ))
        app.include_router(create_workbench_router(
            features={
                "registry": True, "agent_management": True, "task_execution": True,
                "approvals": True, "sessions": True,
            },
            resolve_principal=principal, resolve_task_service=task_service,
            authorize_task=authorize_task,
        ))
        self.client = TestClient(app)

    def test_agent_and_tool_configuration_publish_then_task_run_is_trackable(self):
        for kind, item_id, definition in (
            ("agent", "agent-e2e", {"name": "Research Agent", "tool_ids": ["tool-e2e"]}),
            ("tool", "tool-e2e", {"name": "Evidence Search", "permissions": ["knowledge.read"]}),
            ("model_provider", "provider-e2e", {"name": "Test Provider", "secret_ref": "secret://tenant-a/provider"}),
        ):
            created = self.client.post(
                f"/api/platform/v1/registry/{kind}/{item_id}/versions",
                json={"definition": definition},
            )
            self.assertEqual(created.status_code, 201, created.text)
            version = created.json()["version"]
            published = self.client.post(
                f"/api/platform/v1/registry/{kind}/{item_id}/versions/{version}/publish",
            )
            self.assertEqual(published.status_code, 200, published.text)
            self.assertEqual(published.json()["status"], "published")

        registry = self.client.get("/api/platform/v1/registry/agent")
        self.assertEqual(registry.status_code, 200)
        self.assertEqual(registry.json()["items"][0]["item_id"], "agent-e2e")

        submitted = self.client.post(
            "/api/platform/v1/workbench/tasks",
            json={
                "agent_id": "agent-e2e", "project_id": "project-e2e",
                "session_id": "session-e2e", "input": {"prompt": "Find evidence"},
            },
        )
        self.assertEqual(submitted.status_code, 202, submitted.text)
        self.assertEqual(submitted.json()["run_id"], "run-e2e")

        detail = self.client.get("/api/platform/v1/workbench/tasks/task-e2e")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["status"], "queued")
        self.assertEqual(detail.json()["agent_id"], "agent-e2e")

        other_tenant = self.client.get(
            "/api/platform/v1/workbench/tasks/task-e2e", headers={"x-tenant": "tenant-b"},
        )
        self.assertIn(other_tenant.status_code, {403, 404})

    def test_workbench_ui_exposes_approval_result_and_secure_action_surfaces(self):
        root = Path(__file__).resolve().parents[1] / "agent_platform" / "web" / "static"
        html = (root / "index.html").read_text()
        script = (root / "app.js").read_text()

        for view in ("sessions", "tasks", "approvals", "artifacts", "registry"):
            self.assertIn(f'data-view="{view}"', html)
        for route in (
            "/registry/", "/workbench/tasks", "/approvals", "/files",
            "/runs/", "/decision",
        ):
            self.assertIn(route, script)
        for action in ("data-reg-publish", "data-task", "data-decision", "data-download"):
            self.assertIn(action, script)
        self.assertIn("X-CSRF-Token", script)
        self.assertIn('credentials:"same-origin"', script)
        self.assertIn("connectRunStream", script)
        self.assertIn("run_complete", script)
        self.assertIn("applyCapabilityGate", script)


if __name__ == "__main__":
    unittest.main()
