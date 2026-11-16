from __future__ import annotations

import unittest


class WorkbenchCapabilityTests(unittest.TestCase):
    def test_capabilities_report_only_mounted_services(self):
        try:
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
            from agent_platform.api.workbench import create_workbench_router
        except ImportError:
            self.skipTest("FastAPI is installed in the platform API test environment")

        app = FastAPI()
        app.include_router(create_workbench_router(features={
            "knowledge": True,
            "memory": True,
            "sessions": False,
            "registry": True,
            "agent_management": False,
            "task_execution": False,
            "approvals": False,
            "blackboard": False,
        }))
        response = TestClient(app).get("/api/platform/v1/workbench/capabilities")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["features"], {
            "knowledge": True,
            "memory": True,
            "sessions": False,
            "registry": True,
            "agent_management": False,
            "task_execution": False,
            "approvals": False,
            "blackboard": False,
        })


    def test_task_submission_accepts_and_validates_priority(self):
        try:
            from types import SimpleNamespace
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
            from agent_platform.api.workbench import create_workbench_router
        except ImportError:
            self.skipTest("FastAPI is installed in the platform API test environment")

        captured = {}

        class TaskService:
            tenant_id = "tenant-a"

            async def submit_task(self, **kwargs):
                captured.update(kwargs)
                return {"task_id": "task-1", "priority": kwargs["priority"]}

        async def resolve_principal(_request):
            return SimpleNamespace(tenant_id="tenant-a", user_id="user-a")

        async def resolve_service(_principal):
            return TaskService()

        async def authorize(_principal, _resource, _action):
            return True

        app = FastAPI()
        app.include_router(create_workbench_router(
            features={"task_execution": True},
            resolve_principal=resolve_principal,
            resolve_task_service=resolve_service,
            authorize_task=authorize,
        ))
        client = TestClient(app)
        response = client.post(
            "/api/platform/v1/workbench/tasks",
            json={"agent_id": "researcher", "input": {"text": "hello"}, "priority": 850},
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["priority"], 850)
        self.assertEqual(captured["priority"], 850)

        invalid = client.post(
            "/api/platform/v1/workbench/tasks",
            json={"agent_id": "researcher", "input": {"text": "hello"}, "priority": 1001},
        )
        self.assertEqual(invalid.status_code, 422)

if __name__ == "__main__":
    unittest.main()
