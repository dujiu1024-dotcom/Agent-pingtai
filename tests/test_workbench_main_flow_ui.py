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
        self.assertIn('"model_template"', self.html)
        self.assertIn('"model_provider"', self.html)
        self.assertIn('"tool"', self.html)
        self.assertIn('"skill"', self.html)
        self.assertIn('?"publish":', self.html)
        self.assertIn('x.status==="published"?"disable"', self.html)
        self.assertIn('action==="publish"', self.html)
        self.assertIn('action==="disable"', self.html)
        self.assertIn('instructions:$("agentRole").value', self.html)
        self.assertIn('model_template_id:$("templateRef").value.trim()', self.html)
        self.assertIn('tool_ids:splitIds("agentToolIds")', self.html)
        self.assertIn('skill_ids:splitIds("agentSkillIds")', self.html)

    def test_task_center_uses_authenticated_workbench_routes(self):
        self.assertIn("/workbench/tasks?limit=100", self.html)
        self.assertIn('api("/workbench/tasks"', self.html)
        self.assertIn("/workbench/tasks/"+'"+id', self.html)
        self.assertIn("data-task-action='detail'", self.html)
        self.assertIn("data-task-action='cancel'", self.html)
        self.assertIn("session_id:activeSessionId", self.html)
        self.assertIn("请填写 Agent ID 和任务描述", self.html)

    def test_destructive_actions_require_explicit_confirmation(self):
        self.assertIn("确认取消该任务", self.html)
        self.assertIn("确认删除该知识文档", self.html)
        self.assertIn("确认永久遗忘该记忆", self.html)
        self.assertIn("确认批准这项操作", self.html)
        self.assertIn("确认撤销该审批", self.html)

    def test_blackboard_and_approval_views_remain_capability_gated(self):
        self.assertIn("capabilities.blackboard", self.html)
        self.assertIn("capabilities.approvals", self.html)
        self.assertIn("/blackboard/entries", self.html)
        self.assertIn("/approvals/", self.html)


if __name__ == "__main__":
    unittest.main()
