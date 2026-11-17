"""Persist multi-expert workflow artifacts to the Hybrid Blackboard.

LangGraph controls orchestration; callbacks control expert/model work; this
adapter makes every proposal, conflict, decision, approval and final artifact
durable and idempotent in the injected Blackboard repository.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from agent_platform.blackboard.protocol import BlackboardRepository
from agent_platform.contracts import BlackboardEntry, EntryKind, EntryStatus, Scope
from agent_platform.workflows.expert_registry import ExpertRegistry


class MultiExpertCallbacks(Protocol):
    async def _capture_source_lineage(
        self, *, scope: Scope, scope_id: str, refs: Sequence[str]
    ) -> list[dict[str, Any]]:
        """Pin each conclusion's source to an immutable Blackboard version.

        External evidence identifiers remain explicit opaque references; their
        owning connector/document service is responsible for resolving a version.
        """
        lineage: list[dict[str, Any]] = []
        for ref in refs:
            try:
                normalized_ref = str(UUID(ref))
            except (ValueError, TypeError, AttributeError):
                lineage.append({
                    "reference": str(ref),
                    "source_type": "external",
                    "version": None,
                })
                continue
            source = await self.repository.get(normalized_ref)
            if source is None:
                raise ValueError("Blackboard provenance reference does not exist")
            if source.scope is not scope or source.scope_id != scope_id:
                raise PermissionError("Blackboard provenance crosses the active workflow scope")
            lineage.append({
                "reference": str(ref),
                "source_type": "blackboard",
                "entry_id": source.entry_id,
                "version": source.version,
                "kind": source.kind.value,
                "author_id": source.author_id,
            })
        return lineage

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
        fields = ("scope", "scope_id", "kind", "content", "author_id", "source_refs", "version", "status")
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

    async def _validate_source_refs(
        self, *, scope: Scope, scope_id: str, refs: Sequence[str], require_existing: bool = False
    ) -> None:
        """Reject references to Blackboard entries outside the active workflow scope.

        Non-Blackboard references (for example connector evidence IDs) remain
        opaque and are resolved by their owning resource service.
        """
        for ref in refs:
            try:
                normalized_ref = str(UUID(ref))
            except (ValueError, TypeError, AttributeError):
                # Connector/document IDs are owned by their resource service,
                # not by the UUID-keyed Blackboard repository.
                continue
            source = await self.repository.get(normalized_ref)
            if source is None:
                if require_existing:
                    raise ValueError("Blackboard evidence reference does not exist")
                continue
            if source.scope is not scope or source.scope_id != scope_id:
                raise PermissionError(
                    "Blackboard source reference crosses the active workflow scope"
                )

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

            profile = None
            if self.expert_registry is not None:
                profile = await self._resolve_expert(expert_id, required)
                assignment.update({
                    "expert_name": profile.name,
                    "expert_description": profile.description,
                    "expert_capabilities": sorted(profile.capabilities),
                })
            assignments.append(assignment)
        return assignments

    async def _resolve_expert(self, expert_id: str, required: set[str]):
        if self.expert_registry is None:
            return None
        profile = await self.expert_registry.get(expert_id)
        if profile is None or not profile.enabled:
            raise PermissionError(f"expert is not registered or enabled: {expert_id}")
        missing = required - profile.capabilities
        if missing:
            raise ValueError(
                f"expert {expert_id} lacks required capabilities: {', '.join(sorted(missing))}"
            )
        return profile

    async def run_expert(self, *, assignment: Mapping[str, Any], objective: str, run_context: Mapping[str, Any]) -> Mapping[str, Any]:
        scope, scope_id = self._scope(run_context)
        expert_id = assignment.get("expert_id")
        if not isinstance(expert_id, str) or not expert_id.strip():
            raise ValueError("expert assignment requires a non-empty expert_id")
        raw_required = assignment.get("required_capabilities", ())
        if not isinstance(raw_required, (list, tuple, set, frozenset)) or any(
            not isinstance(item, str) or not item.strip() for item in raw_required
        ):
            raise ValueError("required_capabilities must contain non-empty strings")
        required = set(raw_required)
        if self.expert_registry is not None:
            profile = await self._resolve_expert(expert_id, required)
            # Re-resolve immediately before execution so revocation or a
            # capability change after planning cannot leave a stale assignment usable.
            assignment = {
                **assignment,
                "expert_name": profile.name,
                "expert_description": profile.description,
                "expert_capabilities": sorted(profile.capabilities),
            }
        isolated_context = copy.deepcopy(dict(run_context))
        result = dict(await self.callbacks.run_expert(
            assignment=copy.deepcopy(dict(assignment)),
            objective=objective,
            run_context=isolated_context,
        ))
        expert_id = str(assignment.get("expert_id") or "expert")
        refs = self._refs(result)
        await self._validate_source_refs(scope=scope, scope_id=scope_id, refs=refs)
        entry = BlackboardEntry(
            entry_id=self._entry_id(run_context, "expert:" + expert_id),
            scope=scope, scope_id=scope_id, kind=EntryKind.CLAIM,
            content={"expert_id": expert_id, "contribution": copy.deepcopy(result)},
            author_id=expert_id, source_refs=refs,
        )
        persisted = await self._propose_once(entry)
        result["blackboard_entry_id"] = persisted.entry_id
        return result

    async def synthesize(self, *, objective: str, contributions: Sequence[Mapping[str, Any]], run_context: Mapping[str, Any]) -> Mapping[str, Any]:
        scope, scope_id = self._scope(run_context)
        result = dict(await self.callbacks.synthesize(objective=objective, contributions=contributions, run_context=run_context))
        refs = tuple(str(item["blackboard_entry_id"]) for item in contributions if item.get("blackboard_entry_id"))
        await self._validate_source_refs(scope=scope, scope_id=scope_id, refs=refs)
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
            # Every adjudicated conflict must retain both expert claims and
            # the reviewer's cited evidence, independent of callback omissions.
            refs = tuple(dict.fromkeys((
                *(str(item["blackboard_entry_id"]) for item in contributions if item.get("blackboard_entry_id")),
                *self._refs(conflict),
            )))
            await self._validate_source_refs(scope=scope, scope_id=scope_id, refs=refs)
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
        if not isinstance(decisions, Sequence) or isinstance(decisions, (str, bytes)):
            raise ValueError("conflict resolver must return a sequence of decisions")

        indexed_conflicts: dict[int, Mapping[str, Any]] = {}
        for decision in decisions:
            if not isinstance(decision, Mapping):
                raise ValueError("each conflict decision must be an object")
            selectors = {
                str(decision.get(key)).strip()
                for key in ("conflict_id", "conflict_instance_id", "conflict_key")
                if decision.get(key) is not None and str(decision.get(key)).strip()
            }
            if not selectors:
                raise ValueError("each conflict decision must identify its conflict")
            matches = []
            for index, conflict in enumerate(conflicts):
                conflict_selectors = {
                    str(conflict.get(key)).strip()
                    for key in (
                        "blackboard_entry_id", "id", "conflict_instance_id", "conflict_key"
                    )
                    if conflict.get(key) is not None and str(conflict.get(key)).strip()
                }
                if selectors & conflict_selectors:
                    matches.append(index)
            if len(matches) != 1:
                raise ValueError("conflict decision reference is unknown or ambiguous")
            index = matches[0]
            if index in indexed_conflicts:
                raise ValueError("multiple decisions target the same conflict")
            outcome = decision.get("outcome")
            rationale = decision.get("rationale")
            if not isinstance(outcome, str) or not outcome.strip():
                raise ValueError("conflict decision requires a non-empty outcome")
            if not isinstance(rationale, str) or not rationale.strip():
                raise ValueError("conflict decision requires a non-empty rationale")
            evidence_refs = decision.get("evidence_refs", ())
            if not isinstance(evidence_refs, (list, tuple)):
                raise ValueError("conflict decision evidence_refs must be an array")
            if any(not isinstance(ref, str) or not ref.strip() for ref in evidence_refs):
                raise ValueError("conflict decision evidence_refs must contain non-empty strings")
            indexed_conflicts[index] = decision

        if len(indexed_conflicts) != len(conflicts):
            raise ValueError("conflict resolver must decide every persisted conflict")

        decision_entry_ids = []
        for index, conflict in enumerate(conflicts):
            conflict_entry_id = conflict.get("blackboard_entry_id")
            challenge_entry_id = conflict.get("blackboard_challenge_entry_id")
            if not conflict_entry_id or not challenge_entry_id:
                raise ValueError("conflict is missing its durable conflict or challenge entry")
            persisted_conflict = await self.repository.get(str(conflict_entry_id))
            persisted_challenge = await self.repository.get(str(challenge_entry_id))
            if persisted_conflict is None or persisted_conflict.kind is not EntryKind.CONFLICT:
                raise ValueError("conflict must reference a persisted Blackboard conflict entry")
            if persisted_challenge is None or persisted_challenge.kind is not EntryKind.CHALLENGE:
                raise ValueError("conflict must reference a persisted Blackboard challenge entry")
            decision = indexed_conflicts[index]
            evidence_refs = tuple(decision.get("evidence_refs", ()))
            # Preserve conflict and challenge lineage; UUID evidence must resolve in-scope.
            decision_refs = (str(conflict_entry_id), str(challenge_entry_id), *evidence_refs)
            await self._validate_source_refs(
                scope=scope, scope_id=scope_id, refs=decision_refs,
                require_existing=True,
            )
            source_lineage = await self._capture_source_lineage(
                scope=scope, scope_id=scope_id, refs=decision_refs,
            )
            entry = BlackboardEntry(
                entry_id=self._entry_id(run_context, "decision:" + str(conflict_entry_id)),
                scope=scope, scope_id=scope_id, kind=EntryKind.DECISION,
                content={
                    "conflict_entry_id": conflict_entry_id,
                    "outcome": decision["outcome"].strip(),
                    "rationale": decision["rationale"].strip(),
                    "source_lineage": source_lineage,
                },
                author_id=str(decision.get("decider_id") or "conflict-resolver"),
                source_refs=decision_refs,
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
        await self._validate_source_refs(
            scope=scope, scope_id=scope_id, refs=resolution_refs,
        )
        resolution_lineage = await self._capture_source_lineage(
            scope=scope, scope_id=scope_id, refs=resolution_refs,
        )
        resolved_synthesis["source_lineage"] = resolution_lineage
        resolved_entry = BlackboardEntry(
            entry_id=self._entry_id(run_context, "resolved-synthesis"),
            scope=scope, scope_id=scope_id, kind=EntryKind.ARTIFACT,
            content={
                "resolved_synthesis": copy.deepcopy(resolved_synthesis),
                "source_lineage": resolution_lineage,
            },
            author_id="conflict-resolver", source_refs=resolution_refs,
        )
        persisted_synthesis = await self._propose_once(resolved_entry)
        resolved_synthesis["blackboard_entry_id"] = persisted_synthesis.entry_id
        result["synthesis"] = resolved_synthesis
        return result

    async def prepare_approval(self, *, state: Mapping[str, Any]) -> Mapping[str, Any]:
        request = dict(await self.callbacks.prepare_approval(state=state))
        if not request:
            raise ValueError("approval request must not be empty")
        scope, scope_id = self._scope(state)
        entry = BlackboardEntry(
            entry_id=self._entry_id(state, "approval-request"), scope=scope,
            scope_id=scope_id, kind=EntryKind.NOTE,
            content={"approval_request": copy.deepcopy(request)}, author_id="approval-policy",
        )
        persisted = await self._propose_once(entry)
        return {**request, "blackboard_entry_id": persisted.entry_id}

    async def apply_approval(self, *, state: Mapping[str, Any], decision: Mapping[str, Any]) -> Mapping[str, Any]:
        if not isinstance(decision, Mapping) or not isinstance(decision.get("approved"), bool):
            raise ValueError("approval decision must contain a boolean approved field")
        scope, scope_id = self._scope(state)
        request_id = self._entry_id(state, "approval-request")
        request_entry = await self.repository.get(request_id)
        if (
            request_entry is None
            or request_entry.kind is not EntryKind.NOTE
            or "approval_request" not in request_entry.content
        ):
            raise RuntimeError("a persisted approval request is required before applying a decision")
        await self._validate_source_refs(
            scope=scope, scope_id=scope_id, refs=(request_id,),
        )
        result = dict(await self.callbacks.apply_approval(state=state, decision=decision))
        if not isinstance(result.get("approved"), bool):
            raise ValueError("approval callback must return a boolean approved result")
        approver_id = result.get("approver_id")
        if not isinstance(approver_id, str) or not approver_id.strip():
            raise PermissionError("approval callback must resolve the authenticated approver identity")
        entry = BlackboardEntry(
            entry_id=self._entry_id(state, "approval-result"), scope=scope,
            scope_id=scope_id, kind=EntryKind.DECISION,
            content={
                "approval_result": copy.deepcopy(result),
                "submitted_decision": {"approved": decision["approved"]},
            },
            author_id=approver_id,
            source_refs=(request_id,),
            status=EntryStatus.ACCEPTED if result["approved"] is True else EntryStatus.REJECTED,
        )
        persisted = await self._propose_once(entry)
        result["blackboard_entry_id"] = persisted.entry_id
        return result

    async def archive(self, *, state: Mapping[str, Any]) -> Mapping[str, Any]:
        scope, scope_id = self._scope(state)
        request_id = self._entry_id(state, "approval-request")
        request_entry = await self.repository.get(request_id)
        if request_entry is not None:
            approval = state.get("approval_result")
            if not isinstance(approval, Mapping) or approval.get("approved") is not True:
                raise PermissionError("cannot archive a workflow with a pending or rejected approval")
            approval_id = approval.get("blackboard_entry_id")
            approval_entry = await self.repository.get(str(approval_id)) if approval_id else None
            await self._validate_source_refs(
                scope=scope, scope_id=scope_id, refs=(request_id, str(approval_id or "")),
            )
            approval_payload = (
                approval_entry.content.get("approval_result", {})
                if approval_entry is not None else {}
            )
            if (
                approval_entry is None
                or approval_entry.kind is not EntryKind.DECISION
                or approval_entry.status is not EntryStatus.ACCEPTED
                or request_id not in approval_entry.source_refs
                or not isinstance(approval_payload, Mapping)
                or approval_payload.get("approved") is not True
            ):
                raise PermissionError("accepted approval decision must be persisted before archiving")
        refs = [
            str(item["blackboard_entry_id"])
            for item in state.get("contributions", [])
            if item.get("blackboard_entry_id")
        ]
        if request_entry is not None:
            refs.append(request_id)
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
        # The final artifact is a trust boundary: revalidate every durable
        # reference here, even when earlier workflow stages already checked it.
        await self._validate_source_refs(scope=scope, scope_id=scope_id, refs=refs)
        result = dict(await self.callbacks.archive(state=state))
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
        scope, scope_id = self._scope(state)
        request_id = self._entry_id(state, "approval-request")
        request_entry = await self.repository.get(request_id)
        approval = state.get("approval_result")
        if request_entry is not None:
            if not isinstance(approval, Mapping) or approval.get("approved") is not False:
                raise PermissionError("rejection requires a persisted negative approval decision")
            approval_id = approval.get("blackboard_entry_id")
            approval_entry = await self.repository.get(str(approval_id)) if approval_id else None
            await self._validate_source_refs(
                scope=scope, scope_id=scope_id, refs=(request_id, str(approval_id or "")),
            )
            approval_payload = (
                approval_entry.content.get("approval_result", {})
                if approval_entry is not None else {}
            )
            if (
                approval_entry is None
                or approval_entry.kind is not EntryKind.DECISION
                or approval_entry.status is not EntryStatus.REJECTED
                or request_id not in approval_entry.source_refs
                or not isinstance(approval_payload, Mapping)
                or approval_payload.get("approved") is not False
            ):
                raise PermissionError("negative approval decision must be persisted before rejection")
        refs = (request_id,) if request_entry is not None else ()
        if isinstance(approval, Mapping) and approval.get("blackboard_entry_id"):
            refs = (*refs, str(approval["blackboard_entry_id"]))
        await self._validate_source_refs(scope=scope, scope_id=scope_id, refs=refs)
        result = dict(await self.callbacks.reject(state=state))
        entry = BlackboardEntry(
            entry_id=self._entry_id(state, "rejected-result"), scope=scope,
            scope_id=scope_id, kind=EntryKind.NOTE,
            content={"rejection": result}, author_id="workflow-rejection",
            source_refs=refs, status=EntryStatus.REJECTED,
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
