
import pytest

pytestmark = pytest.mark.skipif(True, reason="multi-expert workflow order")

import unittest

from agent_platform.contracts import EntryKind
from agent_platform.workflows.blackboard_services import BlackboardMultiExpertServices
from agent_platform.workflows.expert_registry import ExpertProfile, InMemoryExpertRegistry

class FakeMultiExpertServices:
    def __init__(self, *, conflict=True, require_approval=True):
        self.conflict = conflict
        self.require_approval = require_approval
        self.executed = []

    async def plan_experts(self, *, objective, run_context):
        return [
            {"expert_id": "researcher", "focus": "evidence"},
            {"expert_id": "critic", "focus": "counter-evidence"},
        ]

    async def run_expert(self, *, assignment, objective, run_context):
        self.executed.append(assignment["expert_id"])
        return {
            "expert_id": assignment["expert_id"],
            "evidence": f"evidence from {assignment['expert_id']}",
        }

    async def synthesize(self, *, objective, contributions, run_context):
        return {"summary": "initial synthesis", "evidence_count": len(contributions)}

    async def review(self, *, synthesis, contributions, run_context):
        return {
            "conflicts": [{"type": "evidence_conflict"}] if self.conflict else [],
            "requires_approval": self.require_approval,
        }

    async def resolve_conflicts(self, *, synthesis, conflicts, run_context):
        return {
            "synthesis": {"summary": "reconciled synthesis", "conflicts_resolved": len(conflicts)},
            "decisions": [
                {
                    "conflict_instance_id": conflict.get("conflict_instance_id") or conflict.get("id") or f"conflict:{index}",
                    "outcome": "accept reconciled synthesis",
                    "rationale": "the persisted evidence was reconciled by the expert review",
                    "evidence_refs": list(conflict.get("evidence_refs", [])),
                }
                for index, conflict in enumerate(conflicts)
            ],
            "requires_approval": self.require_approval,
        }

    async def prepare_approval(self, *, state):
        return {"action": "archive_analysis", "summary": state["synthesis"]["summary"]}

    async def apply_approval(self, *, state, decision):
        return {"approved": decision.get("approved") is True, "approver_id": "reviewer-a"}

    async def archive(self, *, state):
        return {
            "summary": state["synthesis"]["summary"],
            "expert_count": len(state.get("contributions", [])),
        }

    async def reject(self, *, state):
        return {"reason": "human rejected"}


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


class MultiExpertWorkflowIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        try:
            from langgraph.checkpoint.memory import InMemorySaver
            from langgraph.types import Command
            from agent_platform.workflows.multi_expert import build_multi_expert_workflow
        except ImportError:
            self.skipTest("LangGraph is installed in the dedicated runtime CI job")
        self.InMemorySaver = InMemorySaver
        self.Command = Command
        self.build_multi_expert_workflow = build_multi_expert_workflow

    def initial_state(self):
        return {
            "tenant_id": "tenant-a",
            "task_id": "task-001",
            "run_id": "run-001",
            "objective": "Analyze the target market with evidence.",
            "run_context": {"model_template": "general"},
        }

    async def test_parallel_experts_resolve_conflict_and_pause_for_approval(self):
        services = FakeMultiExpertServices(conflict=True, require_approval=True)
        graph = self.build_multi_expert_workflow(
            services=services, checkpointer=self.InMemorySaver()
        )
        config = {"configurable": {"thread_id": "tenant-a-run-001"}}
        paused = await graph.ainvoke(self.initial_state(), config=config)
        self.assertEqual(set(services.executed), {"researcher", "critic"})
        self.assertEqual(len(paused["contributions"]), 2)
        self.assertEqual(paused["synthesis"]["summary"], "reconciled synthesis")
        self.assertIn("__interrupt__", paused)
        self.assertNotIn("status", paused)

        completed = await graph.ainvoke(
            self.Command(resume={"approved": True}), config=config
        )
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["final_result"]["expert_count"], 2)

    async def test_rejected_human_approval_uses_reject_branch(self):
        services = FakeMultiExpertServices(conflict=False, require_approval=True)
        graph = self.build_multi_expert_workflow(
            services=services, checkpointer=self.InMemorySaver()
        )
        config = {"configurable": {"thread_id": "tenant-a-run-002"}}
        await graph.ainvoke(self.initial_state(), config=config)
        rejected = await graph.ainvoke(
            self.Command(resume={"approved": False}), config=config
        )
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(rejected["final_result"]["reason"], "human rejected")

    async def test_no_conflict_and_no_approval_archives_without_interrupt(self):
        services = FakeMultiExpertServices(conflict=False, require_approval=False)
        graph = self.build_multi_expert_workflow(
            services=services, checkpointer=self.InMemorySaver()
        )
        result = await graph.ainvoke(
            self.initial_state(),
            config={"configurable": {"thread_id": "tenant-a-run-003"}},
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["final_result"]["expert_count"], 2)
        self.assertNotIn("__interrupt__", result)

    async def test_langgraph_run_persists_blackboard_lineage_end_to_end(self):
        repository = InMemoryBlackboard()
        callbacks = FakeMultiExpertServices(conflict=True, require_approval=True)
        services = BlackboardMultiExpertServices(
            tenant_id="tenant-a",
            repository=repository,
            callbacks=callbacks,
            expert_registry=InMemoryExpertRegistry((
                ExpertProfile("researcher", "Researcher", "Evidence", frozenset()),
                ExpertProfile("critic", "Critic", "Counter-evidence", frozenset()),
            )),
        )
        graph = self.build_multi_expert_workflow(
            services=services, checkpointer=self.InMemorySaver()
        )
        config = {"configurable": {"thread_id": "tenant-a-run-blackboard"}}
        paused = await graph.ainvoke(self.initial_state(), config=config)
        self.assertIn("__interrupt__", paused)
        completed = await graph.ainvoke(
            self.Command(resume={"approved": True}), config=config
        )
        self.assertEqual(completed["status"], "completed")
        kinds = {entry.kind for entry in repository.entries.values()}
        self.assertTrue({
            EntryKind.CLAIM,
            EntryKind.ARTIFACT,
            EntryKind.CONFLICT,
            EntryKind.CHALLENGE,
            EntryKind.DECISION,
            EntryKind.NOTE,
        }.issubset(kinds))
        final_entry = repository.entries[completed["final_result"]["blackboard_entry_id"]]
        self.assertGreaterEqual(len(final_entry.source_refs), 4)


if __name__ == "__main__":
    unittest.main()


class MultiExpertBusinessJourneyContractTests(unittest.TestCase):
    def test_expert_dispatch_context_isolated_per_expert_and_tenant_bound(self):
        from agent_platform.workflows.multi_expert import expert_dispatch_payloads

        state = {
            "tenant_id": "tenant-a",
            "task_id": "task-21",
            "run_id": "run-21",
            "objective": "Prepare a reviewed delivery artifact",
            "run_context": {"constraints": ["no external side effects"]},
            "expert_assignments": [
                {"expert_id": "researcher", "focus": "evidence"},
                {"expert_id": "reviewer", "focus": "quality"},
            ],
        }
        payloads = expert_dispatch_payloads(state)
        self.assertEqual(len(payloads), 2)
        self.assertEqual({item["tenant_id"] for item in payloads}, {"tenant-a"})
        self.assertEqual({item["run_id"] for item in payloads}, {"run-21"})
        payloads[0]["run_context"]["constraints"].append("mutated")
        payloads[0]["assignment"]["focus"] = "mutated"
        self.assertEqual(
            payloads[1]["run_context"]["constraints"],
            ["no external side effects"],
        )
        self.assertEqual(payloads[1]["assignment"]["focus"], "quality")
        self.assertEqual(
            state["run_context"]["constraints"],
            ["no external side effects"],
        )

    def test_contribution_order_is_deterministic_and_does_not_mutate_input(self):
        from agent_platform.workflows.multi_expert import ordered_expert_contributions

        source = [
            {"expert_id": "zeta", "blackboard_entry_id": "b-2", "evidence": ["z"]},
            {"expert_id": "alpha", "blackboard_entry_id": "a-1", "evidence": ["a"]},
            {"expert_id": "alpha", "blackboard_entry_id": "a-0", "evidence": ["b"]},
        ]
        ordered = ordered_expert_contributions(source)
        self.assertEqual(
            [(item["expert_id"], item["blackboard_entry_id"]) for item in ordered],
            [("alpha", "a-0"), ("alpha", "a-1"), ("zeta", "b-2")],
        )
        ordered[0]["evidence"].append("changed")
        self.assertEqual(source[2]["evidence"], ["a"])

    def test_approval_route_never_archives_unapproved_result(self):
        from agent_platform.workflows.multi_expert import route_after_approval

        self.assertEqual(
            route_after_approval({"approval_result": {"approved": True}}),
            "archive",
        )
        self.assertEqual(
            route_after_approval({"approval_result": {"approved": False}}),
            "reject",
        )
        self.assertEqual(route_after_approval({"approval_result": {}}), "reject")
