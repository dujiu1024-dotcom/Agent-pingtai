import unittest

from agent_platform.approval.policy import (
    ActionRequest,
    RiskLevel,
    SupervisedPolicy,
)
from agent_platform.contracts import (
    BlackboardEntry,
    EntryKind,
    ModelTemplateRef,
    RunContext,
    Scope,
)


class ContractTests(unittest.TestCase):
    def test_blackboard_entry_requires_scope_and_author(self):
        with self.assertRaises(ValueError):
            BlackboardEntry(
                scope=Scope.TASK,
                scope_id=" ",
                kind=EntryKind.CLAIM,
                content={"claim": "x"},
                author_id="agent-a",
            )

    def test_run_context_requires_template(self):
        context = RunContext(
            tenant_id="tenant-a",
            run_id="run-a",
            agent_id="expert-a",
            model_template=ModelTemplateRef("expert-analysis"),
        )
        self.assertEqual(context.model_template.template_id, "expert-analysis")


class SupervisedPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = SupervisedPolicy({
            "read_document": RiskLevel.READ_ONLY,
            "write_draft": RiskLevel.INTERNAL_REVERSIBLE,
            "send_message": RiskLevel.EXTERNAL_OR_IRREVERSIBLE,
            "transfer_funds": RiskLevel.FINANCIAL_OR_SECURITY,
        })

    def request(self, action, approval_id=None, parameters=None):
        return ActionRequest(
            action=action,
            resource="customer:123",
            parameters=parameters or {"text": "draft"},
            actor_id="agent-a",
            approval_id=approval_id,
        )

    def test_read_only_is_automatic(self):
        result = self.policy.evaluate(self.request("read_document"))
        self.assertTrue(result.allowed)
        self.assertFalse(result.requires_approval)

    def test_internal_reversible_is_automatic(self):
        result = self.policy.evaluate(self.request("write_draft"))
        self.assertTrue(result.allowed)

    def test_unknown_tool_is_denied(self):
        result = self.policy.evaluate(self.request("unregistered_tool"))
        self.assertFalse(result.allowed)

    def test_external_action_requires_matching_approval(self):
        request = self.request("send_message", "approval-1")
        denied = self.policy.evaluate(request)
        self.assertFalse(denied.allowed)
        approved = self.policy.evaluate(
            request,
            approved_action_hash=request.digest(),
        )
        self.assertTrue(approved.allowed)

    def test_changed_parameters_invalidate_approval(self):
        request = self.request("send_message", "approval-1", {"text": "changed"})
        result = self.policy.evaluate(request, approved_action_hash="old-digest")
        self.assertFalse(result.allowed)

    def test_financial_and_security_actions_are_never_agent_executed(self):
        request = self.request("transfer_funds", "approval-2")
        result = self.policy.evaluate(
            request,
            approved_action_hash=request.digest(),
        )
        self.assertFalse(result.allowed)
        self.assertTrue(result.requires_approval)


if __name__ == "__main__":
    unittest.main()
