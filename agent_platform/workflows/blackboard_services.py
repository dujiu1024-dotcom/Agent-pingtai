"""Persist multi-expert workflow artifacts to the Hybrid Blackboard.

LangGraph controls orchestration; callbacks control expert/model work; this
adapter makes every proposal, conflict, decision, approval and final artifact
durable and idempotent in the injected Blackboard repository.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

from agent_platform.blackboard.protocol import BlackboardRepository
from agent_platform.contracts import BlackboardEntry, EntryKind, EntryStatus, Scope
from agent_platform.workflows.expert_registry import ExpertRegistry


class MultiExpertCallbacks(Protocol):
    async def plan_experts(self, *, objective: str, run_context: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]: ...
    async def run_expert(self, *, assignment: Mapping[str, Any], objective: str, run_context: Mapping[str, Any]) -> Mapping[str, Any]: ...
    async def synthesize(self, *, objective: str, contributions: Sequence[Mapping[str, Any]], run_context: Mapping[str, Any]) -> Mapping[str, Any]: ...
    async def review(self, *, synthesis: Mapping[str, Any], contributions: Sequence[Mapping[str, Any]], run_context: Mapping[str, Any]) -> Mapping[str, Any]: ...
    async def resolve_conflicts(self, *, synthesis: Mapping[str, Any], conflicts: Sequence[Mapping[str, Any]], run_context: Mapping[str, Any]) -> Mapping[str, Any]: ...
    async def prepare_approval(self, *, state: Mapping[str, Any]) -> Mapping[str, Any]: ...
    async def apply_approval(self, *, state: Mapping[str, Any], decision: Mapping[str, Any]) -> Mapping[str, Any]: ...
    async def archive(self, *, state: Mapping[str, Any]) -> Mapping[str, Any]: ...
    async def reject(self, *, state: Mapping[str, Any]) -> Mapping[str, Any]: ...


class BlackboardMultiExpertServices:
    def __init__(
        self,
        *,
        tenant_id: str,
        repository: BlackboardRepository,
        callbacks: MultiExpertCallbacks,
        expert_registry: ExpertRegistry | None = None,
        max_experts: int = 8,
    ) -> None:
        if not tenant_id.strip():
            raise ValueError("tenant_id must not be empty")
        if isinstance(max_experts, bool) or not 1 <= max_experts <= 32:
            raise ValueError("max_experts must be between 1 and 32")
        self.tenant_id = tenant_id
        self.repository = repository
        self.callbacks = callbacks
        self.expert_registry = expert_registry
        self.max_experts = max_experts

    def _entry_id(self, state: Mapping[str, Any], key: str) -> str:
        run_id = str(state.get("run_id") or state.get("thread_id") or "").strip()
        if not run_id:
            raise ValueError("Blackboard workflow context requires run_id or thread_id")
        scope, scope_id = self._scope(state)
        identity = ":".join((
            "agent-platform", self.tenant_id, scope.value, scope_id, run_id, key,
        ))
        return str(uuid5(NAMESPACE_URL, identity))

    def _scope(self, state: Mapping[str, Any]) -> tuple[Scope, str]:
        if state.get("tenant_id") != self.tenant_id:
            raise PermissionError("workflow tenant does not match Blackboard tenant")
        run_id = str(state.get("run_id") or state.get("thread_id") or "").strip()
        if not run_id:
            raise ValueError("Blackboard workflow context requires run_id or thread_id")
        task_id = state.get("task_id")
        if task_id:
            return Scope.TASK, str(task_id)
        return Scope.RUN, run_id

    @staticmethod
    def _assert_same_entry(existing: BlackboardEntry, proposed: BlackboardEntry) -> None:
        # Deterministic IDs make retries idempotent only when the retried
        # operation has the same semantic payload. Never silently accept a
        # different model result under an already-used run/stage key.
        fields = ("scope", "scope_id", "kind", "content", "author_id", "source_refs")
        if any(getattr(existing, field) != getattr(proposed, field) for field in fields):
            raise RuntimeError(
                f"Blackboard idempotency conflict for entry {proposed.entry_id}"
            )

    async def _propose_once(self, entry: BlackboardEntry) -> BlackboardEntry:
        existing = await self.repository.get(entry.entry_id)
        if existing is not None:
            self._assert_same_entry(existing, entry)
            return existing
        try:
            return await self.repository.propose(entry)
        except Exception:
            # A concurrent retry may have won the unique insert; only suppress
            # the error when the durable entry is now present and identical.
            existing = await self.repository.get(entry.entry_id)
            if existing is not None:
                self._assert_same_entry(existing, entry)
                return existing
            raise

    @staticmethod
    def _refs(value: Mapping[str, Any]) -> tuple[str, ...]:
        refs = value.get("source_refs", value.get("evidence_refs", ()))
        if not isinstance(refs, (list, tuple)):
            return ()
        return tuple(str(ref) for ref in refs if str(ref).strip())

    async def plan_experts(self, *, objective: str, run_context: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
        self._scope(run_context)
        raw_assignments = await self.callbacks.plan_experts(
            objective=objective, run_context=run_context
        )
        if not isinstance(raw_assignments, Sequence) or isinstance(
            raw_assignments, (str, bytes)
        ):
            raise ValueError("expert planner must return a sequence of assignments")
        if len(raw_assignments) > self.max_experts:
            raise ValueError("expert fan-out exceeds the configured limit")

        assignments: list[dict[str, Any]] = []
        seen_expert_ids: set[str] = set()
        for raw_assignment in raw_assignments:
            if not isinstance(raw_assignment, Mapping):
                raise ValueError("each expert assignment must be an object")
            assignment = dict(raw_assignment)
            expert_id = assignment.get("expert_id")
            if not isinstance(expert_id, str) or not expert_id.strip():
                raise ValueError("each expert assignment requires a non-empty expert_id")
            if expert_id in seen_expert_ids:
                raise ValueError(f"duplicate expert assignment: {expert_id}")
            seen_expert_ids.add(expert_id)

            raw_required = assignment.get("required_capabilities", ())
            if not isinstance(raw_required, (list, tuple, set, frozenset)):
                raise ValueError("required_capabilities must be an array")
            if any(not isinstance(item, str) or not item.strip() for item in raw_required):
                raise ValueError("required_capabilities must contain non-empty strings")
            required = set(raw_required)
            if len(required) != len(raw_required):
                raise ValueError("required_capabilities must not contain duplicates")
            assignment["required_capabilities"] = sorted(required)

            if self.expert_registry is not None:
                profile = await self.expert_registry.get(expert_id)
                if profile is None or not profile.enabled:
                    raise PermissionError(
                        f"expert is not registered or enabled: {expert_id}"
                    )
                if not required.issubset(profile.capabilities):
                    missing = ", ".join(sorted(required - profile.capabilities))
                    raise ValueError(
                        f"expert {expert_id} lacks required capabilities: {missing}"
                    )
            assignments.append(assignment)
        return assignments

    async def run_expert(self, *, assignment: Mapping[str, Any], objective: str, run_context: Mapping[str, Any]) -> Mapping[str, Any]:
        scope, scope_id = self._scope(run_context)
        result = dict(await self.callbacks.run_expert(assignment=assignment, objective=objective, run_context=run_context))
        expert_id = str(assignment.get("expert_id") or "expert")
        entry = BlackboardEntry(
            entry_id=self._entry_id(run_context, "expert:" + expert_id),
            scope=scope, scope_id=scope_id, kind=EntryKind.CLAIM,
            content={"expert_id": expert_id, "contribution": copy.deepcopy(result)},
            author_id=expert_id, source_refs=self._refs(result),
        )
        persisted = await self._propose_once(entry)
        result["blackboard_entry_id"] = persisted.entry_id
        return result

    async def synthesize(self, *, objective: str, contributions: Sequence[Mapping[str, Any]], run_context: Mapping[str, Any]) -> Mapping[str, Any]:
        scope, scope_id = self._scope(run_context)
        result = dict(await self.callbacks.synthesize(objective=objective, contributions=contributions, run_context=run_context))
        refs = tuple(str(item["blackboard_entry_id"]) for item in contributions if item.get("blackboard_entry_id"))
        entry = BlackboardEntry(
            entry_id=self._entry_id(run_context, "synthesis"), scope=scope, scope_id=scope_id,
            kind=EntryKind.ARTIFACT, content={"synthesis": copy.deepcopy(result)},
            author_id="synthesizer", source_refs=refs,
        )
        persisted = await self._propose_once(entry)
        result["blackboard_entry_id"] = persisted.entry_id
        return result

    async def review(self, *, synthesis: Mapping[str, Any], contributions: Sequence[Mapping[str, Any]], run_context: Mapping[str, Any]) -> Mapping[str, Any]:
        scope, scope_id = self._scope(run_context)
        result = dict(await self.callbacks.review(synthesis=synthesis, contributions=contributions, run_context=run_context))
        conflicts = []
        for index, raw in enumerate(result.get("conflicts", [])):
            conflict = dict(raw)
            key = str(conflict.get("id") or conflict.get("conflict_key") or "conflict") + ":" + str(index)
            conflict["conflict_instance_id"] = key
            refs = self._refs(conflict) or tuple(str(item["blackboard_entry_id"]) for item in contributions if item.get("blackboard_entry_id"))
            entry = BlackboardEntry(
                entry_id=self._entry_id(run_context, "conflict:" + key),
                scope=scope, scope_id=scope_id, kind=EntryKind.CONFLICT,
                content={"conflict": copy.deepcopy(conflict)}, author_id="reviewer", source_refs=refs,
            )
            persisted = await self._propose_once(entry)
            conflict["blackboard_entry_id"] = persisted.entry_id
            challenge = BlackboardEntry(
                entry_id=self._entry_id(run_context, "challenge:" + key),
                scope=scope, scope_id=scope_id, kind=EntryKind.CHALLENGE,
                content={
                    "conflict_entry_id": persisted.entry_id,
                    "reason": str(conflict.get("reason") or "expert evidence requires review"),
                    "evidence_refs": list(refs),
                },
                author_id="reviewer", source_refs=refs,
            )
            persisted_challenge = await self._propose_once(challenge)
            conflict["blackboard_challenge_entry_id"] = persisted_challenge.entry_id
            conflicts.append(conflict)
        result["conflicts"] = conflicts
        return result

    async def resolve_conflicts(self, *, synthesis: Mapping[str, Any], conflicts: Sequence[Mapping[str, Any]], run_context: Mapping[str, Any]) -> Mapping[str, Any]:
        scope, scope_id = self._scope(run_context)
        result = dict(await self.callbacks.resolve_conflicts(synthesis=synthesis, conflicts=conflicts, run_context=run_context))
        decisions = result.get("decisions", [])
        decision_entry_ids = []
        for conflict in conflicts:
            conflict_entry_id = conflict.get("blackboard_entry_id")
            if not conflict_entry_id:
                raise ValueError("conflict is missing its durable Blackboard entry")
            decision: dict[str, Any] = next((item for item in decisions if item.get("conflict_id") == conflict_entry_id or item.get("conflict_id") == conflict.get("id") or item.get("conflict_instance_id") == conflict.get("conflict_instance_id") or item.get("conflict_key") == conflict.get("conflict_key")), {})
            evidence_refs = decision.get("evidence_refs", conflict.get("evidence_refs", []))
            entry = BlackboardEntry(
                entry_id=self._entry_id(run_context, "decision:" + str(conflict_entry_id)),
                scope=scope, scope_id=scope_id, kind=EntryKind.DECISION,
                content={
                    "conflict_entry_id": conflict_entry_id,
                    "outcome": str(decision.get("outcome") or "resolved"),
                    "rationale": str(decision.get("rationale") or result.get("rationale") or ""),
                },
                author_id=str(decision.get("decider_id") or "conflict-resolver"),
                source_refs=tuple(str(ref) for ref in evidence_refs),
                status=EntryStatus.ACCEPTED,
            )
            persisted = await self._propose_once(entry)
            decision_entry_ids.append(persisted.entry_id)
        resolved_synthesis = dict(result.get("synthesis", synthesis))
        parent_synthesis_id = str(synthesis.get("blackboard_entry_id") or "")
        if parent_synthesis_id:
            resolved_synthesis.setdefault("parent_blackboard_entry_id", parent_synthesis_id)
        resolved_synthesis["blackboard_decision_entry_ids"] = decision_entry_ids
        resolution_refs = tuple(str(item["blackboard_entry_id"]) for item in conflicts if item.get("blackboard_entry_id")) + tuple(str(item["blackboard_challenge_entry_id"]) for item in conflicts if item.get("blackboard_challenge_entry_id")) + tuple(decision_entry_ids) + ((parent_synthesis_id,) if parent_synthesis_id else ())
        resolved_entry = BlackboardEntry(
            entry_id=self._entry_id(run_context, "resolved-synthesis"),
            scope=scope, scope_id=scope_id, kind=EntryKind.ARTIFACT,
            content={"resolved_synthesis": copy.deepcopy(resolved_synthesis)},
            author_id="conflict-resolver", source_refs=resolution_refs,
        )
        persisted_synthesis = await self._propose_once(resolved_entry)
        resolved_synthesis["blackboard_entry_id"] = persisted_synthesis.entry_id
        result["synthesis"] = resolved_synthesis
        return result

    async def prepare_approval(self, *, state: Mapping[str, Any]) -> Mapping[str, Any]:
        request = dict(await self.callbacks.prepare_approval(state=state))
        scope, scope_id = self._scope(state)
        entry = BlackboardEntry(
            entry_id=self._entry_id(state, "approval-request"), scope=scope,
            scope_id=scope_id, kind=EntryKind.NOTE,
            content={"approval_request": request}, author_id="approval-policy",
        )
        await self._propose_once(entry)
        return request

    async def apply_approval(self, *, state: Mapping[str, Any], decision: Mapping[str, Any]) -> Mapping[str, Any]:
        result = dict(await self.callbacks.apply_approval(state=state, decision=decision))
        scope, scope_id = self._scope(state)
        entry = BlackboardEntry(
            entry_id=self._entry_id(state, "approval-result"), scope=scope,
            scope_id=scope_id, kind=EntryKind.DECISION,
            content={"approval_result": copy.deepcopy(result)}, author_id="human-approver",
            source_refs=(self._entry_id(state, "approval-request"),),
            status=EntryStatus.ACCEPTED if result.get("approved") is True else EntryStatus.REJECTED,
        )
        persisted = await self._propose_once(entry)
        result["blackboard_entry_id"] = persisted.entry_id
        return result

    async def archive(self, *, state: Mapping[str, Any]) -> Mapping[str, Any]:
        result = dict(await self.callbacks.archive(state=state))
        scope, scope_id = self._scope(state)
        refs = [
            str(item["blackboard_entry_id"])
            for item in state.get("contributions", [])
            if item.get("blackboard_entry_id")
        ]
        synthesis = state.get("synthesis", {})
        if synthesis.get("blackboard_entry_id"):
            refs.append(str(synthesis["blackboard_entry_id"]))
        if synthesis.get("parent_blackboard_entry_id"):
            refs.append(str(synthesis["parent_blackboard_entry_id"]))
        refs.extend(str(item) for item in synthesis.get("blackboard_decision_entry_ids", []))
        refs.extend(
            str(item[key])
            for item in state.get("conflicts", [])
            for key in ("blackboard_entry_id", "blackboard_challenge_entry_id")
            if item.get(key)
        )
        approval = state.get("approval_result", {})
        if approval.get("blackboard_entry_id"):
            refs.append(str(approval["blackboard_entry_id"]))
        entry = BlackboardEntry(
            entry_id=self._entry_id(state, "final-artifact"), scope=scope,
            scope_id=scope_id, kind=EntryKind.ARTIFACT,
            content={"final_result": copy.deepcopy(result)}, author_id="workflow-archive",
            source_refs=tuple(refs), status=EntryStatus.ACCEPTED,
        )
        persisted = await self._propose_once(entry)
        result["blackboard_entry_id"] = persisted.entry_id
        return result

    async def reject(self, *, state: Mapping[str, Any]) -> Mapping[str, Any]:
        result = dict(await self.callbacks.reject(state=state))
        scope, scope_id = self._scope(state)
        entry = BlackboardEntry(
            entry_id=self._entry_id(state, "rejected-result"), scope=scope,
            scope_id=scope_id, kind=EntryKind.NOTE,
            content={"rejection": result}, author_id="workflow-rejection",
            status=EntryStatus.REJECTED,
        )
        await self._propose_once(entry)
        return result


def build_control_plane_backed_blackboard_services(
    *, tenant_id: str, repository: BlackboardRepository,
    callbacks: MultiExpertCallbacks, registry_service: Any,
) -> BlackboardMultiExpertServices:
    """Production composition: expert planning always resolves tenant registry metadata."""
    from agent_platform.workflows.expert_registry import ControlPlaneExpertRegistry

    return BlackboardMultiExpertServices(
        tenant_id=tenant_id,
        repository=repository,
        callbacks=callbacks,
        expert_registry=ControlPlaneExpertRegistry(
            registry_service=registry_service, tenant_id=tenant_id,
        ),
    )
