from __future__ import annotations

import unittest

from agent_platform.contracts import BlackboardEntry, EntryKind, Scope
from agent_platform.workflows.blackboard_services import BlackboardMultiExpertServices
from agent_platform.workflows.expert_registry import ExpertProfile, InMemoryExpertRegistry


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
        return {"approved": decision.get("approved") is True, "approver_id": "reviewer-a"}

    async def archive(self, *, state):
        return {"result": "report archived"}

    async def reject(self, *, state):
        return {"result": "report rejected"}


class BlackboardMultiExpertServicesTests(unittest.IsolatedAsyncioTestCase):
    async def test_workflow_persists_claim_conflict_decision_approval_and_artifact(self):
        repository = InMemoryBlackboard()
        registry = InMemoryExpertRegistry((
            ExpertProfile("researcher", "Researcher", "Market research", frozenset()),
            ExpertProfile("critic", "Critic", "Counter evidence", frozenset()),
        ))
        services = BlackboardMultiExpertServices(
            tenant_id="tenant-a",
            repository=repository,
            callbacks=WorkflowCallbacks(),
            expert_registry=registry,
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
        challenge_id = review["conflicts"][0]["blackboard_challenge_entry_id"]
        self.assertEqual((await repository.get(conflict_id)).kind, EntryKind.CONFLICT)
        self.assertEqual((await repository.get(challenge_id)).kind, EntryKind.CHALLENGE)

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
        self.assertIn("blackboard_entry_id", request)
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
        self.assertIn(resolution["synthesis"]["blackboard_entry_id"], final_entry.source_refs)
        self.assertIn(conflict_id, final_entry.source_refs)
        self.assertIn(challenge_id, final_entry.source_refs)
        self.assertIn(resolution["synthesis"]["blackboard_decision_entry_ids"][0], final_entry.source_refs)
        self.assertIn(approved["blackboard_entry_id"], final_entry.source_refs)
        self.assertIn(request["blackboard_entry_id"], final_entry.source_refs)
        approval_entry = await repository.get(approved["blackboard_entry_id"])
        self.assertEqual(approval_entry.author_id, "reviewer-a")
        self.assertEqual(approval_entry.source_refs, (request["blackboard_entry_id"],))
        kinds = {entry.kind for entry in repository.entries.values()}
        self.assertTrue({EntryKind.CLAIM, EntryKind.ARTIFACT, EntryKind.CONFLICT, EntryKind.CHALLENGE, EntryKind.DECISION, EntryKind.NOTE}.issubset(kinds))

    async def test_approval_requires_a_persisted_request_and_boolean_decision(self):
        services = BlackboardMultiExpertServices(
            tenant_id="tenant-a",
            repository=InMemoryBlackboard(),
            callbacks=WorkflowCallbacks(),
        )
        context = {"tenant_id": "tenant-a", "run_id": "approval-guard"}
        with self.assertRaisesRegex(RuntimeError, "persisted approval request"):
            await services.apply_approval(state=context, decision={"approved": True})
        await services.prepare_approval(state=context)
        with self.assertRaisesRegex(ValueError, "boolean approved"):
            await services.apply_approval(state=context, decision={"approved": "yes"})

    async def test_approval_requires_trusted_approver_identity(self):
        class MissingApproverCallbacks(WorkflowCallbacks):
            async def apply_approval(self, *, state, decision):
                return {"approved": decision["approved"]}

        services = BlackboardMultiExpertServices(
            tenant_id="tenant-a",
            repository=InMemoryBlackboard(),
            callbacks=MissingApproverCallbacks(),
        )
        context = {"tenant_id": "tenant-a", "run_id": "approval-actor"}
        await services.prepare_approval(state=context)
        with self.assertRaisesRegex(PermissionError, "authenticated approver"):
            await services.apply_approval(state=context, decision={"approved": True})

    async def test_archive_rejects_pending_or_rejected_approval(self):
        services = BlackboardMultiExpertServices(
            tenant_id="tenant-a",
            repository=InMemoryBlackboard(),
            callbacks=WorkflowCallbacks(),
        )
        context = {"tenant_id": "tenant-a", "run_id": "approval-pending"}
        await services.prepare_approval(state=context)
        with self.assertRaisesRegex(PermissionError, "pending or rejected approval"):
            await services.archive(state=context)

    async def test_rejected_approval_is_audited_and_cannot_be_archived(self):
        repository = InMemoryBlackboard()
        services = BlackboardMultiExpertServices(
            tenant_id="tenant-a",
            repository=repository,
            callbacks=WorkflowCallbacks(),
        )
        context = {"tenant_id": "tenant-a", "run_id": "approval-rejected"}
        request = await services.prepare_approval(state=context)
        approval = await services.apply_approval(
            state=context, decision={"approved": False}
        )
        state = {**context, "approval_result": approval}
        with self.assertRaisesRegex(PermissionError, "pending or rejected approval"):
            await services.archive(state=state)
        await services.reject(state=state)
        rejection = await repository.get(services._entry_id(state, "rejected-result"))
        self.assertEqual(
            set(rejection.source_refs),
            {request["blackboard_entry_id"], approval["blackboard_entry_id"]},
        )

    async def test_archive_rejects_forged_approval_result_reference(self):
        services = BlackboardMultiExpertServices(
            tenant_id="tenant-a",
            repository=InMemoryBlackboard(),
            callbacks=WorkflowCallbacks(),
        )
        context = {"tenant_id": "tenant-a", "run_id": "approval-forged"}
        await services.prepare_approval(state=context)
        state = {
            **context,
            "approval_result": {"approved": True, "blackboard_entry_id": "forged-entry"},
        }
        with self.assertRaisesRegex(PermissionError, "accepted approval decision"):
            await services.archive(state=state)

    async def test_expert_assignments_require_registered_enabled_capabilities(self):
        class CapabilityCallbacks(WorkflowCallbacks):
            async def plan_experts(self, *, objective, run_context):
                return [{
                    "expert_id": "researcher",
                    "required_capabilities": ["market_research", "source_review"],
                }]

        registry = InMemoryExpertRegistry((
            ExpertProfile(
                expert_id="researcher",
                name="Researcher",
                description="Researches market evidence",
                capabilities=frozenset({"market_research", "source_review"}),
            ),
        ))
        services = BlackboardMultiExpertServices(
            tenant_id="tenant-a",
            repository=InMemoryBlackboard(),
            callbacks=CapabilityCallbacks(),
            expert_registry=registry,
        )
        assignments = await services.plan_experts(
            objective="analyze market",
            run_context={"tenant_id": "tenant-a", "run_id": "run-capability"},
        )
        self.assertEqual(assignments[0]["expert_id"], "researcher")

        registry.register(ExpertProfile(
            expert_id="researcher",
            name="Researcher",
            description="Insufficient expert",
            capabilities=frozenset({"market_research"}),
        ))
        with self.assertRaises(ValueError):
            await services.plan_experts(
                objective="analyze market",
                run_context={"tenant_id": "tenant-a", "run_id": "run-capability"},
            )
        registry.register(ExpertProfile(
            expert_id="researcher",
            name="Researcher",
            description="Disabled expert",
            capabilities=frozenset({"market_research", "source_review"}),
            enabled=False,
        ))
        with self.assertRaises(PermissionError):
            await services.plan_experts(
                objective="analyze market",
                run_context={"tenant_id": "tenant-a", "run_id": "run-capability"},
            )

    async def test_rejects_blackboard_source_refs_from_another_workflow_scope(self):
        repository = InMemoryBlackboard()
        foreign = BlackboardEntry(
            scope=Scope.TASK,
            scope_id="another-task",
            kind=EntryKind.EVIDENCE,
            content={"evidence": "private source"},
            author_id="researcher",
        )
        repository.entries[foreign.entry_id] = foreign

        class CrossScopeCallbacks(WorkflowCallbacks):
            async def run_expert(self, *, assignment, objective, run_context):
                return {
                    "claim": "supported claim",
                    "source_refs": [foreign.entry_id],
                }

        services = BlackboardMultiExpertServices(
            tenant_id="tenant-a",
            repository=repository,
            callbacks=CrossScopeCallbacks(),
        )
        with self.assertRaisesRegex(PermissionError, "crosses the active workflow scope"):
            await services.run_expert(
                assignment={"expert_id": "researcher"},
                objective="check scope",
                run_context={
                    "tenant_id": "tenant-a",
                    "task_id": "current-task",
                    "run_id": "current-run",
                },
            )

    async def test_rejects_cross_tenant_workflow_context(self):
        services = BlackboardMultiExpertServices(
            tenant_id="tenant-a",
            repository=InMemoryBlackboard(),
            callbacks=WorkflowCallbacks(),
        )
        with self.assertRaises(PermissionError):
            await services.plan_experts(objective="x", run_context={"tenant_id": "tenant-b"})


    async def test_idempotency_key_rejects_changed_payload(self):
        from dataclasses import replace

        repository = InMemoryBlackboard()
        services = BlackboardMultiExpertServices(
            tenant_id="tenant-a", repository=repository,
            callbacks=WorkflowCallbacks(),
        )
        context = {"tenant_id": "tenant-a", "task_id": "task-1", "run_id": "run-idempotent"}
        await services.run_expert(
            assignment={"expert_id": "researcher"},
            objective="analyze market", run_context=context,
        )
        existing = next(iter(repository.entries.values()))
        changed = replace(
            existing,
            content={"expert_id": "researcher", "contribution": {"claim": "different result"}},
        )
        with self.assertRaisesRegex(RuntimeError, "idempotency conflict"):
            await services._propose_once(changed)


    async def test_entry_ids_are_tenant_scoped_and_require_run_identity(self):
        first = BlackboardMultiExpertServices(
            tenant_id="tenant-a", repository=InMemoryBlackboard(),
            callbacks=WorkflowCallbacks(),
        )
        second = BlackboardMultiExpertServices(
            tenant_id="tenant-b", repository=InMemoryBlackboard(),
            callbacks=WorkflowCallbacks(),
        )
        context_a = {"tenant_id": "tenant-a", "run_id": "same-run"}
        context_b = {"tenant_id": "tenant-b", "run_id": "same-run"}
        entry_a = await first.run_expert(
            assignment={"expert_id": "researcher"},
            objective="analyze market", run_context=context_a,
        )
        entry_b = await second.run_expert(
            assignment={"expert_id": "researcher"},
            objective="analyze market", run_context=context_b,
        )
        self.assertNotEqual(entry_a["blackboard_entry_id"], entry_b["blackboard_entry_id"])
        with self.assertRaisesRegex(ValueError, "requires run_id or thread_id"):
            await first.plan_experts(
                objective="analyze market", run_context={"tenant_id": "tenant-a"},
            )


    async def test_expert_planning_bounds_fanout_and_rejects_duplicate_ids(self):
        class Planner(WorkflowCallbacks):
            async def plan_experts(self, *, objective, run_context):
                return [{"expert_id": f"expert-{i}"} for i in range(3)]

        services = BlackboardMultiExpertServices(
            tenant_id="tenant-a",
            repository=InMemoryBlackboard(),
            callbacks=Planner(),
            max_experts=2,
        )
        with self.assertRaisesRegex(ValueError, "fan-out"):
            await services.plan_experts(
                objective="bounded", run_context={"tenant_id": "tenant-a", "run_id": "run"}
            )

        class DuplicatePlanner(WorkflowCallbacks):
            async def plan_experts(self, *, objective, run_context):
                return [{"expert_id": "researcher"}, {"expert_id": "researcher"}]

        duplicate_services = BlackboardMultiExpertServices(
            tenant_id="tenant-a",
            repository=InMemoryBlackboard(),
            callbacks=DuplicatePlanner(),
        )
        with self.assertRaisesRegex(ValueError, "duplicate expert"):
            await duplicate_services.plan_experts(
                objective="deduplicate", run_context={"tenant_id": "tenant-a", "run_id": "run"}
            )

    async def test_expert_planning_rejects_malformed_assignments_and_capabilities(self):
        class InvalidPlanner(WorkflowCallbacks):
            async def plan_experts(self, *, objective, run_context):
                return [{"expert_id": "researcher", "required_capabilities": "admin"}]

        services = BlackboardMultiExpertServices(
            tenant_id="tenant-a",
            repository=InMemoryBlackboard(),
            callbacks=InvalidPlanner(),
        )
        with self.assertRaisesRegex(ValueError, "required_capabilities must be an array"):
            await services.plan_experts(
                objective="validate", run_context={"tenant_id": "tenant-a", "run_id": "run"}
            )

    async def test_max_experts_configuration_is_bounded(self):
        with self.assertRaisesRegex(ValueError, "between 1 and 32"):
            BlackboardMultiExpertServices(
                tenant_id="tenant-a",
                repository=InMemoryBlackboard(),
                callbacks=WorkflowCallbacks(),
                max_experts=33,
            )


    async def test_conflict_resolution_requires_one_explicit_decision_per_conflict(self):
        class MissingDecisionCallbacks(WorkflowCallbacks):
            async def resolve_conflicts(self, *, synthesis, conflicts, run_context):
                return {"synthesis": dict(synthesis), "decisions": []}

        services = BlackboardMultiExpertServices(
            tenant_id="tenant-a",
            repository=InMemoryBlackboard(),
            callbacks=MissingDecisionCallbacks(),
        )
        context = {"tenant_id": "tenant-a", "task_id": "task", "run_id": "missing-decision"}
        contribution = await services.run_expert(
            assignment={"expert_id": "researcher"},
            objective="resolve evidence",
            run_context=context,
        )
        synthesis = await services.synthesize(
            objective="resolve evidence", contributions=[contribution], run_context=context
        )
        review = await services.review(
            synthesis=synthesis, contributions=[contribution], run_context=context
        )
        with self.assertRaisesRegex(ValueError, "decide every persisted conflict"):
            await services.resolve_conflicts(
                synthesis=synthesis, conflicts=review["conflicts"], run_context=context
            )

    async def test_conflict_resolution_rejects_duplicate_or_incomplete_decisions(self):
        class DuplicateDecisionCallbacks(WorkflowCallbacks):
            async def resolve_conflicts(self, *, synthesis, conflicts, run_context):
                decision = {
                    "conflict_key": "market-growth",
                    "outcome": "accept",
                    "rationale": "source is authoritative",
                }
                return {"synthesis": dict(synthesis), "decisions": [decision, decision]}

        services = BlackboardMultiExpertServices(
            tenant_id="tenant-a",
            repository=InMemoryBlackboard(),
            callbacks=DuplicateDecisionCallbacks(),
        )
        context = {"tenant_id": "tenant-a", "task_id": "task", "run_id": "duplicate-decision"}
        contribution = await services.run_expert(
            assignment={"expert_id": "researcher"},
            objective="resolve evidence",
            run_context=context,
        )
        synthesis = await services.synthesize(
            objective="resolve evidence", contributions=[contribution], run_context=context
        )
        review = await services.review(
            synthesis=synthesis, contributions=[contribution], run_context=context
        )
        with self.assertRaisesRegex(ValueError, "multiple decisions"):
            await services.resolve_conflicts(
                synthesis=synthesis, conflicts=review["conflicts"], run_context=context
            )

    async def test_conflict_resolution_rejects_missing_rationale(self):
        class IncompleteDecisionCallbacks(WorkflowCallbacks):
            async def resolve_conflicts(self, *, synthesis, conflicts, run_context):
                return {
                    "synthesis": dict(synthesis),
                    "decisions": [{
                        "conflict_key": "market-growth",
                        "outcome": "accept",
                    }],
                }

        services = BlackboardMultiExpertServices(
            tenant_id="tenant-a",
            repository=InMemoryBlackboard(),
            callbacks=IncompleteDecisionCallbacks(),
        )
        context = {"tenant_id": "tenant-a", "task_id": "task", "run_id": "incomplete-decision"}
        contribution = await services.run_expert(
            assignment={"expert_id": "researcher"},
            objective="resolve evidence",
            run_context=context,
        )
        synthesis = await services.synthesize(
            objective="resolve evidence", contributions=[contribution], run_context=context
        )
        review = await services.review(
            synthesis=synthesis, contributions=[contribution], run_context=context
        )
        with self.assertRaisesRegex(ValueError, "non-empty rationale"):
            await services.resolve_conflicts(
                synthesis=synthesis, conflicts=review["conflicts"], run_context=context
            )


if __name__ == "__main__":
    unittest.main()
