from __future__ import annotations

import unittest

from agent_platform.contracts import EntryKind
from agent_platform.workflows.blackboard_services import BlackboardMultiExpertServices


class InMemoryBlackboard:
    def __init__(self):
        self.entries = {}

    async def get(self, entry_id):
        return self.entries.get(entry_id)

    async def propose(self, entry):
        if entry.entry_id in self.entries:
            raise RuntimeError("duplicate entry")
        self.entries[entry.entry_id] = entry
        return entry


class WorkflowCallbacks:
    async def plan_experts(self, *, objective, run_context):
        return [{"expert_id": "researcher"}, {"expert_id": "critic"}]

    async def run_expert(self, *, assignment, objective, run_context):
        return {
            "expert_id": assignment["expert_id"],
            "claim": "market demand is growing",
            "evidence_refs": ["source:market-report"],
        }

    async def synthesize(self, *, objective, contributions, run_context):
        return {"summary": "market is growing"}

    async def review(self, *, synthesis, contributions, run_context):
        return {
            "conflicts": [{
                "conflict_key": "market-growth",
                "reason": "growth rate differs",
                "evidence_refs": ["source:market-report", "source:filing"],
            }],
            "requires_approval": True,
        }

    async def resolve_conflicts(self, *, synthesis, conflicts, run_context):
        return {
            "synthesis": {"summary": "reconciled market growth"},
            "decisions": [{
                "conflict_key": "market-growth",
                "outcome": "accept conservative estimate",
                "rationale": "filing data has higher authority",
                "evidence_refs": ["source:filing"],
            }],
        }

    async def prepare_approval(self, *, state):
        return {"action": "publish_report"}

    async def apply_approval(self, *, state, decision):
        return {"approved": decision.get("approved") is True}

    async def archive(self, *, state):
        return {"result": "report archived"}

    async def reject(self, *, state):
        return {"result": "report rejected"}


class BlackboardMultiExpertServicesTests(unittest.IsolatedAsyncioTestCase):
    async def test_workflow_persists_claim_conflict_decision_approval_and_artifact(self):
        repository = InMemoryBlackboard()
        services = BlackboardMultiExpertServices(
            tenant_id="tenant-a",
            repository=repository,
            callbacks=WorkflowCallbacks(),
        )
        context = {
            "tenant_id": "tenant-a",
            "task_id": "task-1",
            "run_id": "run-1",
            "model_template": "general",
        }
        assignments = await services.plan_experts(
            objective="analyze market", run_context=context
        )
        first = await services.run_expert(
            assignment=assignments[0],
            objective="analyze market",
            run_context=context,
        )
        repeated = await services.run_expert(
            assignment=assignments[0],
            objective="analyze market",
            run_context=context,
        )
        self.assertEqual(first["blackboard_entry_id"], repeated["blackboard_entry_id"])
        self.assertEqual(len(repository.entries), 1)

        contributions = [first]
        synthesis = await services.synthesize(
            objective="analyze market",
            contributions=contributions,
            run_context=context,
        )
        review = await services.review(
            synthesis=synthesis,
            contributions=contributions,
            run_context=context,
        )
        self.assertEqual(len(review["conflicts"]), 1)
        conflict_id = review["conflicts"][0]["blackboard_entry_id"]
        self.assertEqual((await repository.get(conflict_id)).kind, EntryKind.CONFLICT)

        resolution = await services.resolve_conflicts(
            synthesis=synthesis,
            conflicts=review["conflicts"],
            run_context=context,
        )
        decisions = [entry for entry in repository.entries.values() if entry.kind is EntryKind.DECISION]
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].content["outcome"], "accept conservative estimate")

        state = {
            **context,
            "contributions": contributions,
            "synthesis": resolution["synthesis"],
            "conflicts": review["conflicts"],
        }
        request = await services.prepare_approval(state=state)
        self.assertEqual(request["action"], "publish_report")
        approved = await services.apply_approval(
            state=state, decision={"approved": True}
        )
        self.assertTrue(approved["approved"])
        state["approval_result"] = approved
        archived = await services.archive(state=state)
        self.assertIn("blackboard_entry_id", archived)
        final_entry = await repository.get(archived["blackboard_entry_id"])
        self.assertIn(first["blackboard_entry_id"], final_entry.source_refs)
        self.assertIn(synthesis["blackboard_entry_id"], final_entry.source_refs)
        self.assertIn(conflict_id, final_entry.source_refs)
        self.assertIn(resolution["synthesis"]["blackboard_decision_entry_ids"][0], final_entry.source_refs)
        self.assertIn(approved["blackboard_entry_id"], final_entry.source_refs)
        kinds = {entry.kind for entry in repository.entries.values()}
        self.assertTrue({EntryKind.CLAIM, EntryKind.ARTIFACT, EntryKind.CONFLICT, EntryKind.DECISION, EntryKind.NOTE}.issubset(kinds))

    async def test_rejects_cross_tenant_workflow_context(self):
        services = BlackboardMultiExpertServices(
            tenant_id="tenant-a",
            repository=InMemoryBlackboard(),
            callbacks=WorkflowCallbacks(),
        )
        with self.assertRaises(PermissionError):
            await services.plan_experts(objective="x", run_context={"tenant_id": "tenant-b"})


if __name__ == "__main__":
    unittest.main()
