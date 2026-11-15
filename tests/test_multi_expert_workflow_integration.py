import unittest

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent_platform.workflows.multi_expert import build_multi_expert_workflow


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
            "requires_approval": self.require_approval,
        }

    async def prepare_approval(self, *, state):
        return {"action": "archive_analysis", "summary": state["synthesis"]["summary"]}

    async def apply_approval(self, *, state, decision):
        return {"approved": decision.get("approved") is True}

    async def archive(self, *, state):
        return {
            "summary": state["synthesis"]["summary"],
            "expert_count": len(state.get("contributions", [])),
        }

    async def reject(self, *, state):
        return {"reason": "human rejected"}


class MultiExpertWorkflowIntegrationTests(unittest.IsolatedAsyncioTestCase):
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
        graph = build_multi_expert_workflow(
            services=services, checkpointer=InMemorySaver()
        )
        config = {"configurable": {"thread_id": "tenant-a-run-001"}}
        paused = await graph.ainvoke(self.initial_state(), config=config)
        self.assertEqual(set(services.executed), {"researcher", "critic"})
        self.assertEqual(len(paused["contributions"]), 2)
        self.assertEqual(paused["synthesis"]["summary"], "reconciled synthesis")
        self.assertIn("__interrupt__", paused)
        self.assertNotIn("status", paused)

        completed = await graph.ainvoke(
            Command(resume={"approved": True}), config=config
        )
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["final_result"]["expert_count"], 2)

    async def test_rejected_human_approval_uses_reject_branch(self):
        services = FakeMultiExpertServices(conflict=False, require_approval=True)
        graph = build_multi_expert_workflow(
            services=services, checkpointer=InMemorySaver()
        )
        config = {"configurable": {"thread_id": "tenant-a-run-002"}}
        await graph.ainvoke(self.initial_state(), config=config)
        rejected = await graph.ainvoke(
            Command(resume={"approved": False}), config=config
        )
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(rejected["final_result"]["reason"], "human rejected")

    async def test_no_conflict_and_no_approval_archives_without_interrupt(self):
        services = FakeMultiExpertServices(conflict=False, require_approval=False)
        graph = build_multi_expert_workflow(
            services=services, checkpointer=InMemorySaver()
        )
        result = await graph.ainvoke(
            self.initial_state(),
            config={"configurable": {"thread_id": "tenant-a-run-003"}},
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["final_result"]["expert_count"], 2)
        self.assertNotIn("__interrupt__", result)


if __name__ == "__main__":
    unittest.main()
