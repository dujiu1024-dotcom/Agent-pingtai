import unittest

from agent_platform.workflows.multi_expert import (
    route_after_approval,
    route_after_resolution,
    route_after_review,
)


class MultiExpertRoutingTests(unittest.TestCase):
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
