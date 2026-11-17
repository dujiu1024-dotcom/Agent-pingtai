from __future__ import annotations

import asyncio
import unittest
from typing import Any

from agent_platform.contracts import BlackboardEntry, EntryKind, EntryStatus
from agent_platform.workflows.blackboard_services import BlackboardMultiExpertServices
from agent_platform.workflows.multi_expert import build_multi_expert_workflow


class AcceptanceBlackboard:
    def __init__(self) -> None:
        self.entries: dict[str, BlackboardEntry] = {}

    async def get(self, entry_id: str) -> BlackboardEntry | None:
        return self.entries.get(entry_id)

    async def propose(self, entry: BlackboardEntry) -> BlackboardEntry:
        existing = self.entries.get(entry.entry_id)
        if existing is not None:
            raise RuntimeError("duplicate entry")
        self.entries[entry.entry_id] = entry
        return entry


class EndToEndCallbacks:
    def __init__(self) -> None:
        self.started_experts: set[str] = set()
        self.both_experts_started = asyncio.Event()
        self.stages: list[str] = []

    async def plan_experts(self, *, objective: str, run_context: Any):
        self.stages.append("dispatch")
        return [{"expert_id": "researcher"}, {"expert_id": "critic"}]

    async def run_expert(self, *, assignment: Any, objective: str, run_context: Any):
        expert_id = assignment["expert_id"]
        self.started_experts.add(expert_id)
        if len(self.started_experts) == 2:
            self.both_experts_started.set()
        await asyncio.wait_for(self.both_experts_started.wait(), timeout=3)
        self.stages.append("parallel:" + expert_id)
        return {
            "expert_id": expert_id,
            "claim": "market growth estimate from " + expert_id,
            "evidence_refs": ["source:" + expert_id],
        }

    async def synthesize(self, *, objective: str, contributions: Any, run_context: Any):
        self.stages.append("proposal")
        assert len(contributions) == 2
        return {"summary": "combined market assessment"}

    async def review(self, *, synthesis: Any, contributions: Any, run_context: Any):
        self.stages.append("challenge")
        assert len(contributions) == 2
        return {
            "conflicts": [{
                "conflict_key": "growth-estimate",
                "reason": "experts reported different growth estimates",
                "evidence_refs": ["source:researcher", "source:critic"],
            }],
            "requires_approval": True,
        }

    async def resolve_conflicts(self, *, synthesis: Any, conflicts: Any, run_context: Any):
        self.stages.append("adjudication")
        assert len(conflicts) == 1
        assert conflicts[0].get("blackboard_entry_id")
        assert conflicts[0].get("blackboard_challenge_entry_id")
        return {
            "synthesis": {"summary": "conservative estimate accepted"},
            "decisions": [{
                "conflict_key": "growth-estimate",
                "outcome": "accept-conservative-estimate",
                "rationale": "the more conservative source is better corroborated",
                "evidence_refs": ["source:researcher"],
            }],
            "requires_approval": True,
        }

    async def prepare_approval(self, *, state: Any):
        self.stages.append("approval-request")
        return {"action": "archive_market_assessment"}

    async def apply_approval(self, *, state: Any, decision: Any):
        self.stages.append("approval")
        return {
            "approved": decision.get("approved") is True,
            "approver_id": "human-reviewer-1",
        }

    async def archive(self, *, state: Any):
        self.stages.append("archive")
        assert state["approval_result"]["approved"] is True
        return {"artifact": "market-assessment-v1"}

    async def reject(self, *, state: Any):
        self.stages.append("reject")
        return {"reason": "not approved"}


class Issue13A1301EndToEndAcceptance(unittest.IsolatedAsyncioTestCase):
    async def test_dispatch_parallel_propose_challenge_adjudicate_approve_archive(self):
        try:
            from langgraph.checkpoint.memory import InMemorySaver
            from langgraph.types import Command
        except ImportError as exc:
            self.skipTest(f"LangGraph test dependencies unavailable: {exc}")

        repository = AcceptanceBlackboard()
        callbacks = EndToEndCallbacks()
        services = BlackboardMultiExpertServices(
            tenant_id="tenant-a",
            repository=repository,
            callbacks=callbacks,
        )
        graph = build_multi_expert_workflow(
            services=services,
            checkpointer=InMemorySaver(),
        )
        config = {"configurable": {"thread_id": "a-13-01-e2e-thread"}}
        initial = {
            "tenant_id": "tenant-a",
            "task_id": "task-a13-01",
            "run_id": "run-a13-01",
            "objective": "reconcile market growth estimates",
            "run_context": {"source": "acceptance-test"},
        }

        interrupted = await graph.ainvoke(initial, config=config)
        self.assertIn("__interrupt__", interrupted)
        self.assertEqual(callbacks.started_experts, {"researcher", "critic"})
        self.assertEqual(len([
            entry for entry in repository.entries.values()
            if entry.kind is EntryKind.CLAIM
        ]), 2)
        self.assertEqual(len([
            entry for entry in repository.entries.values()
            if entry.kind is EntryKind.CHALLENGE
        ]), 1)
        self.assertEqual(len([
            entry for entry in repository.entries.values()
            if entry.kind is EntryKind.DECISION
        ]), 1)
        self.assertNotIn("archive", callbacks.stages)

        completed = await graph.ainvoke(Command(resume={"approved": True}), config=config)
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["final_result"]["artifact"], "market-assessment-v1")
        self.assertLess(callbacks.stages.index("dispatch"), callbacks.stages.index("proposal"))
        self.assertLess(callbacks.stages.index("proposal"), callbacks.stages.index("challenge"))
        self.assertLess(callbacks.stages.index("challenge"), callbacks.stages.index("adjudication"))
        self.assertLess(callbacks.stages.index("adjudication"), callbacks.stages.index("approval-request"))
        self.assertLess(callbacks.stages.index("approval-request"), callbacks.stages.index("approval"))
        self.assertLess(callbacks.stages.index("approval"), callbacks.stages.index("archive"))

        final_id = completed["final_result"]["blackboard_entry_id"]
        final_entry = await repository.get(final_id)
        self.assertIsNotNone(final_entry)
        self.assertEqual(final_entry.kind, EntryKind.ARTIFACT)
        self.assertEqual(final_entry.status, EntryStatus.ACCEPTED)
        self.assertEqual(len(final_entry.source_refs), 8)
        self.assertTrue(all(awaitable is not None for awaitable in [
            await repository.get(ref) for ref in final_entry.source_refs
        ]))


if __name__ == "__main__":
    unittest.main()
