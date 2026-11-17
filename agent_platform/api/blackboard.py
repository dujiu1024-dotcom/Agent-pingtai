"""Tenant-authenticated HTTP API for Hybrid Blackboard entries.

The host application supplies principal, tenant-bound repository, and resource
scope authorization. Request bodies never select a tenant or actor identity.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Awaitable, Callable, Sequence

from fastapi import APIRouter, Header, HTTPException, Request

from agent_platform.blackboard.sse import (
    decode_blackboard_cursor,
    stream_blackboard_events,
)

from agent_platform.blackboard.protocol import BlackboardRepository, VersionConflict
from agent_platform.contracts import BlackboardEntry, EntryKind, EntryStatus, Scope
from agent_platform.identity.models import Principal
from agent_platform.streaming.sse import sse_response

PrincipalResolver = Callable[[Request], Awaitable[Principal]]
RepositoryResolver = Callable[[Principal], Awaitable[BlackboardRepository]]
ScopeAuthorizer = Callable[[Principal, Scope, str, str], Awaitable[bool]]
EvidenceReferenceVerifier = Callable[[Principal, Scope, str, Sequence[str]], Awaitable[bool]]
EventReplayResolver = Callable[[Principal], Awaitable[Any]]


def create_blackboard_router(
    *,
    resolve_principal: PrincipalResolver,
    resolve_repository: RepositoryResolver,
    authorize_scope: ScopeAuthorizer,
    verify_evidence_refs: EvidenceReferenceVerifier | None = None,
    resolve_event_replay: EventReplayResolver | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/blackboard", tags=["blackboard"])

    async def repository_for(principal: Principal):
        repository = await resolve_repository(principal)
        bound_tenant = getattr(repository, "tenant_id", None)
        if bound_tenant != principal.tenant_id:
            raise HTTPException(403, "Blackboard repository tenant mismatch")
        return repository

    async def scope_is_authorized(
        principal: Principal, scope: Scope, scope_id: str, action: str
    ) -> bool:
        # Tenant resources are addressed by the authenticated tenant, not by
        # a tenant identifier supplied by a caller. Project/task membership is
        # resolved by the host's resource-level authorization policy.
        if scope is Scope.TENANT and scope_id != principal.tenant_id:
            return False
        return await authorize_scope(principal, scope, scope_id, action)

    async def verify_refs(principal: Principal, scope: Scope, scope_id: str, refs: Sequence[str]) -> tuple[str, ...]:
        normalized = tuple(dict.fromkeys(ref.strip() for ref in refs if ref.strip()))
        if not normalized:
            return ()
        if verify_evidence_refs is None:
            raise HTTPException(503, "evidence reference verification is not configured")
        if not await verify_evidence_refs(principal, scope, scope_id, normalized):
            raise HTTPException(422, "one or more Blackboard evidence references are missing or inaccessible")
        return normalized

    async def scope_context(request: Request, scope: Scope, scope_id: str, action: str):
        principal = await resolve_principal(request)
        if not await scope_is_authorized(principal, scope, scope_id, action):
            raise HTTPException(403, "Blackboard scope access denied")
        return principal, await repository_for(principal)

    async def target_context(request: Request, entry_id: str, action: str):
        principal = await resolve_principal(request)
        repository = await repository_for(principal)
        entry = await repository.get(entry_id)
        if entry is None:
            raise HTTPException(404, "Blackboard entry not found")
        if not await scope_is_authorized(principal, entry.scope, entry.scope_id, action):
            raise HTTPException(403, "Blackboard scope access denied")
        return principal, repository, entry

    def serialize(entry: BlackboardEntry) -> dict[str, Any]:
        return {
            "entry_id": entry.entry_id,
            "scope": entry.scope.value,
            "scope_id": entry.scope_id,
            "kind": entry.kind.value,
            "content": entry.content,
            "author_id": entry.author_id,
            "source_refs": list(entry.source_refs),
            "version": entry.version,
            "status": entry.status.value,
            "created_at": entry.created_at.isoformat(),
            "correlation_id": entry.correlation_id,
        }

    @router.get("/events/stream")
    async def stream_events(
        request: Request,
        scope: Scope,
        scope_id: str,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ):
        if not scope_id.strip():
            raise HTTPException(400, "scope_id is required")
        principal, _ = await scope_context(request, scope, scope_id, "read")
        if resolve_event_replay is None:
            raise HTTPException(503, "Blackboard event replay is not configured")
        replay = await resolve_event_replay(principal)
        if getattr(replay, "tenant_id", None) != principal.tenant_id:
            raise HTTPException(403, "Blackboard event replay tenant mismatch")
        try:
            cursor = decode_blackboard_cursor(last_event_id)
        except ValueError as exc:
            raise HTTPException(400, "Last-Event-ID is invalid") from exc
        return sse_response(
            stream_blackboard_events(
                replay=replay, scope=scope, scope_id=scope_id, cursor=cursor,
            )
        )

    @router.get("/entries")
    async def list_entries(request: Request, scope: Scope, scope_id: str, kinds: str = "", limit: int = 100):
        if not scope_id.strip() or not 1 <= limit <= 200:
            raise HTTPException(400, "scope_id is required and limit must be 1..200")
        _, repository = await scope_context(request, scope, scope_id, "read")
        kind_values = [item.strip() for item in kinds.split(",") if item.strip()]
        allowed = {item.value for item in EntryKind}
        if any(item not in allowed for item in kind_values):
            raise HTTPException(400, "unknown Blackboard entry kind")
        entries = await repository.list_entries(scope=scope, scope_id=scope_id, kinds=kind_values, limit=limit)
        return {"items": [serialize(entry) for entry in entries]}

    @router.post("/entries", status_code=201)
    async def propose_entry(request: Request):
        principal = await resolve_principal(request)
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise TypeError("JSON object required")
            scope = Scope(body.get("scope"))
            kind = EntryKind(body.get("kind"))
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, "valid scope and kind are required") from exc
        scope_id = str(body.get("scope_id") or "").strip()
        content = body.get("content")
        refs = body.get("source_refs", [])
        if not scope_id or not isinstance(content, dict):
            raise HTTPException(400, "scope_id and object content are required")
        if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
            raise HTTPException(400, "source_refs must be an array of strings")
        if not await scope_is_authorized(principal, scope, scope_id, "write"):
            raise HTTPException(403, "Blackboard scope access denied")
        refs = await verify_refs(principal, scope, scope_id, refs)
        repository = await repository_for(principal)
        entry = BlackboardEntry(scope=scope, scope_id=scope_id, kind=kind, content=content,
                                 author_id=principal.user_id, source_refs=tuple(refs),
                                 status=EntryStatus.PROPOSED)
        try:
            persisted = await repository.propose(entry)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(409, str(exc)) from exc
        return serialize(persisted)

    @router.get("/entries/{entry_id}/history")
    async def entry_history(entry_id: str, request: Request):
        _, repository, entry = await target_context(request, entry_id, "read")
        list_versions = getattr(repository, "list_versions", None)
        if list_versions is None:
            raise HTTPException(501, "Blackboard repository does not expose version history")
        versions = await list_versions(entry.entry_id)
        return {"items": [serialize(version) for version in versions]}

    @router.get("/entries/{entry_id}")
    async def get_entry(entry_id: str, request: Request):
        _, _, entry = await target_context(request, entry_id, "read")
        return serialize(entry)

    @router.post("/entries/{entry_id}/challenges", status_code=201)
    async def challenge_entry(entry_id: str, request: Request):
        principal, repository, entry = await target_context(request, entry_id, "write")
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "JSON object required")
        reason = str(body.get("reason") or "").strip()
        refs = body.get("evidence_refs", [])
        if not reason or not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
            raise HTTPException(400, "reason and evidence_refs are required")
        refs = await verify_refs(principal, entry.scope, entry.scope_id, refs)
        challenged = await repository.record_challenge(entry.entry_id, principal.user_id, reason, refs)
        return serialize(challenged)

    @router.post("/entries/{entry_id}/decisions", status_code=201)
    async def decide_conflict(entry_id: str, request: Request):
        principal, repository, entry = await target_context(request, entry_id, "decide")
        if entry.kind is not EntryKind.CONFLICT:
            raise HTTPException(400, "decisions can only be recorded against conflicts")
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "JSON object required")
        outcome = str(body.get("outcome") or "").strip()
        rationale = str(body.get("rationale") or "").strip()
        refs = body.get("evidence_refs", [])
        if not outcome or not rationale or not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
            raise HTTPException(400, "outcome, rationale and evidence_refs are required")
        refs = await verify_refs(principal, entry.scope, entry.scope_id, refs)
        decision = await repository.record_decision(entry.entry_id, principal.user_id, outcome, rationale, refs)
        return serialize(decision)

    @router.patch("/entries/{entry_id}")
    async def revise_entry(entry_id: str, request: Request):
        principal, repository, entry = await target_context(request, entry_id, "write")
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "JSON object required")
        content = body.get("content")
        expected_version = body.get("expected_version")
        if not isinstance(content, dict) or type(expected_version) is not int or expected_version < 1:
            raise HTTPException(400, "content and positive integer expected_version are required")
        await verify_refs(principal, entry.scope, entry.scope_id, entry.source_refs)
        replacement = replace(entry, content=content, author_id=principal.user_id)
        try:
            revised = await repository.revise(entry.entry_id, expected_version=expected_version, replacement=replacement)
        except VersionConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except (KeyError, ValueError) as exc:
            raise HTTPException(404, str(exc)) from exc
        return serialize(revised)

    return router
