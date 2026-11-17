from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace

from agent_platform.blackboard.protocol import VersionConflict
from agent_platform.contracts import BlackboardEntry, EntryKind, Scope
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


class TraceabilityCallbacks:
    def __init__(self, evidence_id):
        self.evidence_id = evidence_id

    async def plan_experts(self, *, objective, run_context):
        return [{"expert_id": "researcher"}, {"expert_id": "critic"}]

    async def run_expert(self, *, assignment, objective, run_context):
        expert_id = assignment["expert_id"]
        return {
            "expert_id": expert_id,
            "claim": f"{expert_id} verified the market trend",
            "evidence_refs": ["external:annual-report:2026"],
        }

    async def synthesize(self, *, objective, contributions, run_context):
        return {"summary": "market trend is positive"}

    async def review(self, *, synthesis, contributions, run_context):
        return {
            "conflicts": [{
                "conflict_key": "growth-rate",
                "reason": "experts used different reporting periods",
                "evidence_refs": [self.evidence_id, "external:filing:2026-Q3"],
            }]
        }

    async def resolve_conflicts(self, *, synthesis, conflicts, run_context):
        return {
            "synthesis": {"summary": "use the audited quarterly rate"},
            "decisions": [{
                "conflict_instance_id": conflicts[0]["conflict_instance_id"],
                "outcome": "accept audited rate",
                "rationale": "audited filing is period-aligned and authoritative",
                "evidence_refs": [self.evidence_id],
            }],
        }

    async def prepare_approval(self, *, state):
        return {"action": "publish"}

    async def apply_approval(self, *, state, decision):
        return {"approved": bool(decision.get("approved")), "approver_id": "reviewer"}

    async def archive(self, *, state):
        return {"archived": True}

    async def reject(self, *, state):
        return {"rejected": True}


class VersionedInMemoryBlackboard:
    """Deterministic optimistic-concurrency adapter for the repository contract."""

    def __init__(self, entry):
        self.chains = {entry.entry_id: [entry]}
        self.lock = asyncio.Lock()

    async def revise(self, entry_id, *, expected_version, replacement):
        async with self.lock:
            current = self.chains[entry_id][-1]
            if current.version != expected_version:
                raise VersionConflict(
                    f"expected version {expected_version}, found {current.version}"
                )
            revised = replace(replacement, version=expected_version + 1)
            self.chains[entry_id].append(revised)
            return revised


class Issue13A1302TraceabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_adjudicated_conclusion_pins_expert_evidence_and_blackboard_versions(self):
        repository = InMemoryBlackboard()
        evidence = BlackboardEntry(
            scope=Scope.TASK,
            scope_id="task-a-13-02",
            kind=EntryKind.EVIDENCE,
            content={"title": "Audited quarterly filing", "period": "2026-Q3"},
            author_id="evidence-ingestor",
            version=4,
        )
        repository.entries[evidence.entry_id] = evidence
        services = BlackboardMultiExpertServices(
            tenant_id="tenant-a",
            repository=repository,
            callbacks=TraceabilityCallbacks(evidence.entry_id),
        )
        context = {
            "tenant_id": "tenant-a",
            "task_id": "task-a-13-02",
            "run_id": "run-a-13-02",
        }

        assignments = await services.plan_experts(
            objective="assess market growth", run_context=context
        )
        contributions = [
            await services.run_expert(
                assignment=assignment,
                objective="assess market growth",
                run_context=context,
            )
            for assignment in assignments
        ]
        synthesis = await services.synthesize(
            objective="assess market growth",
            contributions=contributions,
            run_context=context,
        )
        review = await services.review(
            synthesis=synthesis,
            contributions=contributions,
            run_context=context,
        )
        resolution = await services.resolve_conflicts(
            synthesis=synthesis,
            conflicts=review["conflicts"],
            run_context=context,
        )

        decision_id = resolution["synthesis"]["blackboard_decision_entry_ids"][0]
        decision = await repository.get(decision_id)
        self.assertEqual(decision.kind, EntryKind.DECISION)
        self.assertEqual(decision.version, 1)
        self.assertEqual(decision.status.value, "accepted")
        self.assertIn("audited filing is period-aligned", decision.content["rationale"])

        claim_ids = {item["blackboard_entry_id"] for item in contributions}
        self.assertTrue(claim_ids.issubset(set(decision.source_refs)))
        self.assertIn(evidence.entry_id, decision.source_refs)
        self.assertIn("external:filing:2026-Q3", decision.source_refs)

        lineage = decision.content["source_lineage"]
        blackboard_lineage = {
            item["entry_id"]: item for item in lineage
            if item["source_type"] == "blackboard"
        }
        self.assertEqual(blackboard_lineage[evidence.entry_id]["version"], 4)
        self.assertEqual(blackboard_lineage[evidence.entry_id]["author_id"], "evidence-ingestor")
        for claim_id in claim_ids:
            claim = await repository.get(claim_id)
            self.assertEqual(blackboard_lineage[claim_id]["version"], claim.version)
            self.assertEqual(blackboard_lineage[claim_id]["author_id"], claim.author_id)
            self.assertIn(claim.content["contribution"]["claim"], {
                "researcher verified the market trend",
                "critic verified the market trend",
            })

        external = next(item for item in lineage if item.get("reference") == "external:filing:2026-Q3")
        self.assertEqual(external, {
            "reference": "external:filing:2026-Q3",
            "source_type": "external",
            "version": None,
        })

        resolved_entry = await repository.get(resolution["synthesis"]["blackboard_entry_id"])
        self.assertEqual(
            resolved_entry.content["source_lineage"],
            resolution["synthesis"]["source_lineage"],
        )
        resolved_sources = {
            item["entry_id"]: item for item in resolved_entry.content["source_lineage"]
            if item["source_type"] == "blackboard"
        }
        self.assertEqual(resolved_sources[decision_id]["version"], decision.version)
        self.assertEqual(resolved_sources[evidence.entry_id]["version"], evidence.version)
        self.assertTrue(claim_ids.issubset(resolved_sources))

    async def test_stale_concurrent_revision_returns_explainable_version_conflict(self):
        original = BlackboardEntry(
            scope=Scope.TASK,
            scope_id="task-concurrency",
            kind=EntryKind.CLAIM,
            content={"claim": "initial"},
            author_id="researcher",
            version=1,
        )
        repository = VersionedInMemoryBlackboard(original)
        first = replace(original, content={"claim": "writer one"})
        second = replace(original, content={"claim": "writer two"})

        results = await asyncio.gather(
            repository.revise(original.entry_id, expected_version=1, replacement=first),
            repository.revise(original.entry_id, expected_version=1, replacement=second),
            return_exceptions=True,
        )

        successes = [item for item in results if not isinstance(item, Exception)]
        conflicts = [item for item in results if isinstance(item, VersionConflict)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(successes[0].version, 2)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(str(conflicts[0]), "expected version 1, found 2")
        self.assertEqual(
            [item.version for item in repository.chains[original.entry_id]],
            [1, 2],
        )


if __name__ == "__main__":
    unittest.main()
