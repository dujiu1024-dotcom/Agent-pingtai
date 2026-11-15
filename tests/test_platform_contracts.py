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
        self.policy = SupervisedPolicy()

    def request(self, risk, approval_id=None):
        return ActionRequest(
            action="send_message",
            resource="customer:123",
            risk=risk,
            parameters={"text": "draft"},
            actor_id="agent-a",
            approval_id=approval_id,
        )

    def test_read_only_is_automatic(self):
        result = self.policy.evaluate(self.request(RiskLevel.READ_ONLY))
        self.assertTrue(result.allowed)
        self.assertFalse(result.requires_approval)

    def test_internal_reversible_is_automatic(self):
        result = self.policy.evaluate(self.request(RiskLevel.INTERNAL_REVERSIBLE))
        self.assertTrue(result.allowed)

    def test_external_action_requires_matching_approval(self):
        request = self.request(RiskLevel.EXTERNAL_OR_IRREVERSIBLE, "approval-1")
        denied = self.policy.evaluate(request)
        self.assertFalse(denied.allowed)
        approved = self.policy.evaluate(
            request,
            approved_action_hash="same",
            requested_action_hash="same",
        )
        self.assertTrue(approved.allowed)

    def test_changed_parameters_invalidate_approval(self):
        request = self.request(RiskLevel.EXTERNAL_OR_IRREVERSIBLE, "approval-1")
        result = self.policy.evaluate(
            request,
            approved_action_hash="old",
            requested_action_hash="new",
        )
        self.assertFalse(result.allowed)

    def test_financial_and_security_actions_are_never_agent_executed(self):
        request = self.request(RiskLevel.FINANCIAL_OR_SECURITY, "approval-2")
        result = self.policy.evaluate(
            request,
            approved_action_hash="same",
            requested_action_hash="same",
        )
        self.assertFalse(result.allowed)
        self.assertTrue(result.requires_approval)


if __name__ == "__main__":
    unittest.main()
