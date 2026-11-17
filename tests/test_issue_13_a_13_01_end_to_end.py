from __future__ import annotations

import unittest

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent_platform.contracts import EntryKind
from agent_platform.workflows.blackboard_services import BlackboardMultiExpertServices
from agent_platform.workflows.multi_expert import build_multi_expert_workflow


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


class EndToEndCallbacks:
    def __init__(self):
        self.executed_experts = []
        self.synthesized_contributions = []
        self.resolved_conflicts = []

    async def plan_experts(self, *, objective, run_context):
        return [
            {"expert_id": "researcher", "required_capabilities": ["research"]},
            {"expert_id": "critic", "required_capabilities": ["review"]},
        ]

    async def run_expert(self, *, assignment, objective, run_context):
        expert_id = assignment["expert_id"]
        self.executed_experts.append(expert_id)
        return {
            "expert_id": expert_id,
            "claim": f"{expert_id} contribution",
            "evidence_refs": [f"source:{expert_id}"],
        }

    async def synthesize(self, *, objective, contributions, run_context):
        self.synthesized_contributions = list(contributions)
        return {"summary": "combined findings"}

    async def review(self, *, synthesis, contributions, run_context):
        return {
            "conflicts": [{
                "conflict_key": "market-growth",
                "reason": "experts interpret growth differently",
                "evidence_refs": ["source:researcher", "source:critic"],
            }],
            "requires_approval": True,
        }

    async def resolve_conflicts(self, *, synthesis, conflicts, run_context):
        self.resolved_conflicts = list(conflicts)
        return {
            "synthesis": {"summary": "reconciled findings"},
            "decisions": [{
                "conflict_key": "market-growth",
                "outcome": "accept",
                "rationale": "prefer independently corroborated evidence",
                "evidence_refs": ["source:researcher"],
            }],
        }

    async def prepare_approval(self, *, state):
        return {"action": "publish_report", "summary": state["synthesis"]["summary"]}

    async def apply_approval(self, *, state, decision):
        return {
            "approved": decision.get("approved") is True,
            "approver_id": "authenticated-reviewer",
        }

    async def archive(self, *, state):
        return {"result": "report archived"}

    async def reject(self, *, state):
        return {"result": "report rejected"}


class MultiExpertEndToEndAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_dispatch_parallel_proposal_challenge_resolution_approval_archive(self):
        repository = InMemoryBlackboard()
        callbacks = EndToEndCallbacks()
        services = BlackboardMultiExpertServices(
            tenant_id="tenant-acceptance",
            repository=repository,
            callbacks=callbacks,
        )
        graph = build_multi_expert_workflow(
            services=services,
            checkpointer=InMemorySaver(),
        )
        config = {"configurable": {"thread_id": "a-13-01-run"}}
        initial_state = {
            "tenant_id": "tenant-acceptance",
            "task_id": "a-13-01-task",
            "run_id": "a-13-01-run",
            "objective": "assess market growth",
            "run_context": {"request_id": "acceptance"},
        }

        interrupted = await graph.ainvoke(initial_state, config=config)

        self.assertCountEqual(callbacks.executed_experts, ["researcher", "critic"])
        self.assertEqual(len(callbacks.synthesized_contributions), 2)
        self.assertEqual(len(callbacks.resolved_conflicts), 1)
        self.assertTrue(interrupted.get("__interrupt__"))
        self.assertNotIn("final_result", interrupted)

        completed = await graph.ainvoke(
            Command(resume={"approved": True}),
            config=config,
        )

        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["final_result"]["result"], "report archived")

        entries = list(repository.entries.values())
        kinds = {entry.kind for entry in entries}
        self.assertTrue({
            EntryKind.CLAIM,
            EntryKind.ARTIFACT,
            EntryKind.CONFLICT,
            EntryKind.CHALLENGE,
            EntryKind.DECISION,
            EntryKind.NOTE,
        }.issubset(kinds))

        claims = [entry for entry in entries if entry.kind is EntryKind.CLAIM]
        self.assertEqual({entry.author_id for entry in claims}, {"researcher", "critic"})
        archive = await repository.get(completed["final_result"]["blackboard_entry_id"])
        self.assertEqual(archive.content["final_result"]["result"], "report archived")
        referenced = set(archive.source_refs)
        self.assertEqual(len(referenced), len(archive.source_refs))
        self.assertTrue(all(entry.entry_id in referenced for entry in claims))
        self.assertTrue(any(entry.kind is EntryKind.CONFLICT and entry.entry_id in referenced for entry in entries))
        self.assertTrue(any(entry.kind is EntryKind.CHALLENGE and entry.entry_id in referenced for entry in entries))
        self.assertTrue(any(entry.kind is EntryKind.DECISION and entry.entry_id in referenced for entry in entries))
        self.assertTrue(any(entry.kind is EntryKind.NOTE and entry.entry_id in referenced for entry in entries))


if __name__ == "__main__":
    unittest.main()
