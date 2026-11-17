import unittest

from agent_platform.workflows.multi_expert import ordered_expert_contributions


class ExpertAggregationTests(unittest.TestCase):
    def test_contributions_are_sorted_independent_of_completion_order(self):
        first = [
            {"expert_id": "zeta", "blackboard_entry_id": "entry-z", "claim": "z"},
            {"expert_id": "alpha", "blackboard_entry_id": "entry-a", "claim": "a"},
            {"expert_id": "beta", "blackboard_entry_id": "entry-b", "claim": "b"},
        ]
        second = list(reversed(first))

        expected = ["alpha", "beta", "zeta"]
        self.assertEqual(
            [item["expert_id"] for item in ordered_expert_contributions(first)],
            expected,
        )
        self.assertEqual(
            [item["expert_id"] for item in ordered_expert_contributions(second)],
            expected,
        )

    def test_aggregation_copies_nested_payloads_before_callbacks_receive_them(self):
        source = [{"expert_id": "alpha", "claim": {"facts": ["one"]}}]
        aggregated = ordered_expert_contributions(source)
        aggregated[0]["claim"]["facts"].append("mutated")
        self.assertEqual(source[0]["claim"]["facts"], ["one"])

    def test_equal_expert_ids_use_blackboard_entry_as_stable_tiebreaker(self):
        source = [
            {"expert_id": "alpha", "blackboard_entry_id": "b"},
            {"expert_id": "alpha", "blackboard_entry_id": "a"},
        ]
        self.assertEqual(
            [item["blackboard_entry_id"] for item in ordered_expert_contributions(source)],
            ["a", "b"],
        )


if __name__ == "__main__":
    unittest.main()
