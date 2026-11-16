from __future__ import annotations

import unittest
from pathlib import Path


class WorkbenchMainFlowUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (
            Path(__file__).resolve().parents[1] / "platform-ui" / "index.html"
        ).read_text()

    def test_agent_model_tool_flows_use_versioned_registry(self):
        self.assertIn("/registry/agent?limit=100", self.html)
        self.assertIn("/registry/agent/", self.html)
        self.assertIn("/registry/model_template", self.html)
        self.assertIn("/registry/model_provider", self.html)
        self.assertIn("/registry/tool", self.html)
        self.assertIn("/registry/skill", self.html)
        self.assertIn("data-registry-action='publish'", self.html)
        self.assertIn("data-registry-action='disable'", self.html)

    def test_task_center_uses_authenticated_workbench_routes(self):
        self.assertIn("/workbench/tasks?limit=100", self.html)
        self.assertIn('api("/workbench/tasks"', self.html)
        self.assertIn("/workbench/tasks/"+'"+id', self.html)
        self.assertIn("data-task-action='detail'", self.html)
        self.assertIn("data-task-action='cancel'", self.html)

    def test_blackboard_and_approval_views_remain_capability_gated(self):
        self.assertIn("capabilities.blackboard", self.html)
        self.assertIn("capabilities.approvals", self.html)
        self.assertIn("/blackboard/entries", self.html)
        self.assertIn("/approvals/", self.html)


if __name__ == "__main__":
    unittest.main()
