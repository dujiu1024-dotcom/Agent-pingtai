from __future__ import annotations

import unittest

from agent_platform.contracts import EntryKind
from agent_platform.workflows.blackboard_services import BlackboardMultiExpertServices
from tests.test_blackboard_multi_expert_services import InMemoryBlackboard, WorkflowCallbacks


class BlackboardReplayTests(unittest.IsolatedAsyncioTestCase):
    async def test_replayed_workflow_stages_are_idempotent_and_payload_bound(self):
        repository = InMemoryBlackboard()
        services = BlackboardMultiExpertServices(
            tenant_id="tenant-a", repository=repository,
            callbacks=WorkflowCallbacks(),
        )
        context = {
            "tenant_id": "tenant-a", "task_id": "task-replay",
            "run_id": "run-replay", "model_template": "general",
        }
        assignments = await services.plan_experts(
            objective="analyze the market", run_context=context,
        )
        contribution = await services.run_expert(
            assignment=assignments[0], objective="analyze the market",
            run_context=context,
        )
        synthesis = await services.synthesize(
            objective="analyze the market", contributions=[contribution],
            run_context=context,
        )
        replayed_synthesis = await services.synthesize(
            objective="analyze the market", contributions=[contribution],
            run_context=context,
        )
        self.assertEqual(
            synthesis["blackboard_entry_id"],
            replayed_synthesis["blackboard_entry_id"],
        )

        review = await services.review(
            synthesis=synthesis, contributions=[contribution], run_context=context,
        )
        replayed_review = await services.review(
            synthesis=synthesis, contributions=[contribution], run_context=context,
        )
        self.assertEqual(
            review["conflicts"][0]["blackboard_entry_id"],
            replayed_review["conflicts"][0]["blackboard_entry_id"],
        )
        self.assertEqual(
            review["conflicts"][0]["blackboard_challenge_entry_id"],
            replayed_review["conflicts"][0]["blackboard_challenge_entry_id"],
        )

        resolution = await services.resolve_conflicts(
            synthesis=synthesis, conflicts=review["conflicts"], run_context=context,
        )
        replayed_resolution = await services.resolve_conflicts(
            synthesis=synthesis, conflicts=review["conflicts"], run_context=context,
        )
        self.assertEqual(
            resolution["synthesis"]["blackboard_entry_id"],
            replayed_resolution["synthesis"]["blackboard_entry_id"],
        )
        decision_entries = [
            entry for entry in repository.entries.values()
            if entry.kind is EntryKind.DECISION
        ]
        self.assertEqual(len(decision_entries), 1)

        state = {
            **context, "contributions": [contribution],
            "synthesis": resolution["synthesis"], "conflicts": review["conflicts"],
        }
        before_approval_replay = len(repository.entries)
        approval = await services.prepare_approval(state=state)
        after_first_approval = len(repository.entries)
        replayed_approval = await services.prepare_approval(state=state)
        self.assertEqual(approval, replayed_approval)
        self.assertEqual(after_first_approval, before_approval_replay + 1)
        self.assertEqual(len(repository.entries), after_first_approval)
        state["approval_result"] = await services.apply_approval(
            state=state, decision={"approved": True},
        )
        archived = await services.archive(state=state)
        replayed_archive = await services.archive(state=state)
        self.assertEqual(
            archived["blackboard_entry_id"],
            replayed_archive["blackboard_entry_id"],
        )
        self.assertEqual(
            len(repository.entries),
            len({entry.entry_id for entry in repository.entries.values()}),
        )


if __name__ == "__main__":
    unittest.main()
