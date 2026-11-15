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


if __name__ == "__main__":
    unittest.main()
