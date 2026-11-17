import unittest

from agent_platform.workflows.multi_expert import (
    route_after_approval,
    route_after_resolution,
    route_after_review,
)


class MultiExpertRoutingTests(unittest.TestCase):

    def test_dispatch_payloads_isolate_context_and_allow_missing_task_id(self):
        from agent_platform.workflows.multi_expert import expert_dispatch_payloads

        state = {
            "tenant_id": "tenant-a",
            "run_id": "run-1",
            "objective": "analyze",
            "run_context": {"nested": {"value": "shared"}},
            "expert_assignments": [
                {"expert_id": "researcher"},
                {"expert_id": "critic"},
            ],
        }
        payloads = expert_dispatch_payloads(state)
        self.assertEqual(len(payloads), 2)
        self.assertIsNone(payloads[0]["task_id"])
        self.assertIsNot(payloads[0]["run_context"], payloads[1]["run_context"])
        payloads[0]["run_context"]["nested"]["value"] = "mutated"
        self.assertEqual(payloads[1]["run_context"]["nested"]["value"], "shared")
        self.assertEqual(state["run_context"]["nested"]["value"], "shared")

    def test_review_routes_conflicts_before_approval(self):
        self.assertEqual(
            route_after_review({
                "conflicts": [{"id": "conflict-1"}],
                "requires_approval": True,
            }),
            "resolve_conflicts",
        )

    def test_review_routes_to_human_approval_when_required(self):
        self.assertEqual(
            route_after_review({"conflicts": [], "requires_approval": True}),
            "human_approval",
        )

    def test_review_routes_directly_to_archive(self):
        self.assertEqual(
            route_after_review({"conflicts": [], "requires_approval": False}),
            "archive",
        )

    def test_conflict_resolution_can_require_approval(self):
        self.assertEqual(
            route_after_resolution({"requires_approval": True}),
            "human_approval",
        )

    def test_approved_decision_archives(self):
        self.assertEqual(
            route_after_approval({"approval_result": {"approved": True}}),
            "archive",
        )

    def test_rejected_or_missing_decision_rejects(self):
        self.assertEqual(
            route_after_approval({"approval_result": {"approved": False}}),
            "reject",
        )
        self.assertEqual(route_after_approval({}), "reject")


if __name__ == "__main__":
    unittest.main()
