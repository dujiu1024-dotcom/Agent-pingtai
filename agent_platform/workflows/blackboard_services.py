"""Persist multi-expert workflow artifacts to the Hybrid Blackboard.

LangGraph controls orchestration; callbacks control expert/model work; this
adapter makes every proposal, conflict, decision, approval and final artifact
durable and idempotent in the injected Blackboard repository.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

from agent_platform.blackboard.protocol import BlackboardRepository
from agent_platform.contracts import BlackboardEntry, EntryKind, EntryStatus, Scope


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
    def __init__(self, *, tenant_id: str, repository: BlackboardRepository, callbacks: MultiExpertCallbacks) -> None:
        if not tenant_id.strip():
            raise ValueError("tenant_id must not be empty")
        self.tenant_id = tenant_id
        self.repository = repository
        self.callbacks = callbacks

    @staticmethod
    def _entry_id(run_id: str, key: str) -> str:
        return str(uuid5(NAMESPACE_URL, "agent-platform:" + run_id + ":" + key))

    def _scope(self, state: Mapping[str, Any]) -> tuple[Scope, str]:
        if state.get("tenant_id") != self.tenant_id:
            raise PermissionError("workflow tenant does not match Blackboard tenant")
        task_id = state.get("task_id")
        if task_id:
            return Scope.TASK, str(task_id)
        return Scope.RUN, str(state["run_id"])

    async def _propose_once(self, entry: BlackboardEntry) -> BlackboardEntry:
        existing = await self.repository.get(entry.entry_id)
        if existing is not None:
            return existing
        try:
            return await self.repository.propose(entry)
        except Exception:
            # A concurrent retry may have won the unique insert; only suppress
            # the error when the durable entry is now present.
            existing = await self.repository.get(entry.entry_id)
            if existing is not None:
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
        return await self.callbacks.plan_experts(objective=objective, run_context=run_context)

    async def run_expert(self, *, assignment: Mapping[str, Any], objective: str, run_context: Mapping[str, Any]) -> Mapping[str, Any]:
        result = dict(await self.callbacks.run_expert(assignment=assignment, objective=objective, run_context=run_context))
        run_id = str(run_context.get("run_id") or run_context.get("thread_id") or "run")
        scope, scope_id = self._scope(run_context)
        expert_id = str(assignment.get("expert_id") or "expert")
        entry = BlackboardEntry(
            entry_id=self._entry_id(run_id, "expert:" + expert_id),
            scope=scope, scope_id=scope_id, kind=EntryKind.CLAIM,
            content={"expert_id": expert_id, "contribution": result},
            author_id=expert_id, source_refs=self._refs(result),
        )
        persisted = await self._propose_once(entry)
        result["blackboard_entry_id"] = persisted.entry_id
        return result

    async def synthesize(self, *, objective: str, contributions: Sequence[Mapping[str, Any]], run_context: Mapping[str, Any]) -> Mapping[str, Any]:
        result = dict(await self.callbacks.synthesize(objective=objective, contributions=contributions, run_context=run_context))
        run_id = str(run_context.get("run_id") or run_context.get("thread_id") or "run")
        scope, scope_id = self._scope(run_context)
        refs = tuple(str(item["blackboard_entry_id"]) for item in contributions if item.get("blackboard_entry_id"))
        entry = BlackboardEntry(
            entry_id=self._entry_id(run_id, "synthesis"), scope=scope, scope_id=scope_id,
            kind=EntryKind.ARTIFACT, content={"synthesis": result},
            author_id="synthesizer", source_refs=refs,
        )
        persisted = await self._propose_once(entry)
        result["blackboard_entry_id"] = persisted.entry_id
        return result

    async def review(self, *, synthesis: Mapping[str, Any], contributions: Sequence[Mapping[str, Any]], run_context: Mapping[str, Any]) -> Mapping[str, Any]:
        result = dict(await self.callbacks.review(synthesis=synthesis, contributions=contributions, run_context=run_context))
        run_id = str(run_context.get("run_id") or run_context.get("thread_id") or "run")
        scope, scope_id = self._scope(run_context)
        conflicts = []
        for index, raw in enumerate(result.get("conflicts", [])):
            conflict = dict(raw)
            key = str(conflict.get("conflict_key") or conflict.get("id") or index)
            refs = self._refs(conflict) or tuple(str(item["blackboard_entry_id"]) for item in contributions if item.get("blackboard_entry_id"))
            entry = BlackboardEntry(
                entry_id=self._entry_id(run_id, "conflict:" + key),
                scope=scope, scope_id=scope_id, kind=EntryKind.CONFLICT,
                content={"conflict": conflict}, author_id="reviewer", source_refs=refs,
            )
            persisted = await self._propose_once(entry)
            conflict["blackboard_entry_id"] = persisted.entry_id
            conflicts.append(conflict)
        result["conflicts"] = conflicts
        return result

    async def resolve_conflicts(self, *, synthesis: Mapping[str, Any], conflicts: Sequence[Mapping[str, Any]], run_context: Mapping[str, Any]) -> Mapping[str, Any]:
        result = dict(await self.callbacks.resolve_conflicts(synthesis=synthesis, conflicts=conflicts, run_context=run_context))
        run_id = str(run_context.get("run_id") or run_context.get("thread_id") or "run")
        scope, scope_id = self._scope(run_context)
        decisions = result.get("decisions", [])
        decision_entry_ids = []
        for index, conflict in enumerate(conflicts):
            conflict_entry_id = conflict.get("blackboard_entry_id")
            if not conflict_entry_id:
                raise ValueError("conflict is missing its durable Blackboard entry")
            decision = next((item for item in decisions if item.get("conflict_id") == conflict_entry_id or item.get("conflict_id") == conflict.get("id") or item.get("conflict_key") == conflict.get("conflict_key")), {})
            evidence_refs = decision.get("evidence_refs", conflict.get("evidence_refs", []))
            entry = BlackboardEntry(
                entry_id=self._entry_id(run_id, "decision:" + str(conflict_entry_id)),
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
        resolved_synthesis["blackboard_decision_entry_ids"] = decision_entry_ids
        resolution_refs = tuple(str(item["blackboard_entry_id"]) for item in conflicts if item.get("blackboard_entry_id")) + tuple(decision_entry_ids)
        resolved_entry = BlackboardEntry(
            entry_id=self._entry_id(run_id, "resolved-synthesis"),
            scope=scope, scope_id=scope_id, kind=EntryKind.ARTIFACT,
            content={"resolved_synthesis": resolved_synthesis},
            author_id="conflict-resolver", source_refs=resolution_refs,
        )
        persisted_synthesis = await self._propose_once(resolved_entry)
        resolved_synthesis["blackboard_entry_id"] = persisted_synthesis.entry_id
        result["synthesis"] = resolved_synthesis
        return result

    async def prepare_approval(self, *, state: Mapping[str, Any]) -> Mapping[str, Any]:
        request = dict(await self.callbacks.prepare_approval(state=state))
        run_id = str(state["run_id"])
        scope, scope_id = self._scope(state)
        entry = BlackboardEntry(
            entry_id=self._entry_id(run_id, "approval-request"), scope=scope,
            scope_id=scope_id, kind=EntryKind.NOTE,
            content={"approval_request": request}, author_id="approval-policy",
        )
        await self._propose_once(entry)
        return request

    async def apply_approval(self, *, state: Mapping[str, Any], decision: Mapping[str, Any]) -> Mapping[str, Any]:
        result = dict(await self.callbacks.apply_approval(state=state, decision=decision))
        run_id = str(state["run_id"])
        scope, scope_id = self._scope(state)
        entry = BlackboardEntry(
            entry_id=self._entry_id(run_id, "approval-result"), scope=scope,
            scope_id=scope_id, kind=EntryKind.DECISION,
            content={"approval_result": result}, author_id="human-approver",
            status=EntryStatus.ACCEPTED if result.get("approved") is True else EntryStatus.REJECTED,
        )
        persisted = await self._propose_once(entry)
        result["blackboard_entry_id"] = persisted.entry_id
        return result

    async def archive(self, *, state: Mapping[str, Any]) -> Mapping[str, Any]:
        result = dict(await self.callbacks.archive(state=state))
        run_id = str(state["run_id"])
        scope, scope_id = self._scope(state)
        refs = [
            str(item["blackboard_entry_id"])
            for item in state.get("contributions", [])
            if item.get("blackboard_entry_id")
        ]
        synthesis = state.get("synthesis", {})
        if synthesis.get("blackboard_entry_id"):
            refs.append(str(synthesis["blackboard_entry_id"]))
        refs.extend(str(item) for item in synthesis.get("blackboard_decision_entry_ids", []))
        refs.extend(
            str(item["blackboard_entry_id"])
            for item in state.get("conflicts", [])
            if item.get("blackboard_entry_id")
        )
        approval = state.get("approval_result", {})
        if approval.get("blackboard_entry_id"):
            refs.append(str(approval["blackboard_entry_id"]))
        entry = BlackboardEntry(
            entry_id=self._entry_id(run_id, "final-artifact"), scope=scope,
            scope_id=scope_id, kind=EntryKind.ARTIFACT,
            content={"final_result": result}, author_id="workflow-archive",
            source_refs=tuple(refs), status=EntryStatus.ACCEPTED,
        )
        persisted = await self._propose_once(entry)
        result["blackboard_entry_id"] = persisted.entry_id
        return result

    async def reject(self, *, state: Mapping[str, Any]) -> Mapping[str, Any]:
        result = dict(await self.callbacks.reject(state=state))
        run_id = str(state["run_id"])
        scope, scope_id = self._scope(state)
        entry = BlackboardEntry(
            entry_id=self._entry_id(run_id, "rejected-result"), scope=scope,
            scope_id=scope_id, kind=EntryKind.NOTE,
            content={"rejection": result}, author_id="workflow-rejection",
            status=EntryStatus.REJECTED,
        )
        await self._propose_once(entry)
        return result
