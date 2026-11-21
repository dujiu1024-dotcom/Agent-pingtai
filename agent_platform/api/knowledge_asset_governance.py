"""Tenant-scoped APIs for knowledge asset quality, freshness and ACL propagation."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request

from agent_platform.knowledge.asset_governance import (
    FreshnessPolicy, KnowledgeAssetGovernanceService, QualityAssessment,
    QualityDimension, SourcePermission,
)
from agent_platform.knowledge.contracts import KnowledgePrincipal

PrincipalResolver = Callable[[Request], Awaitable[KnowledgePrincipal]]


def create_knowledge_asset_governance_router(*, service: KnowledgeAssetGovernanceService,
                                              resolve_principal: PrincipalResolver) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/knowledge/assets", tags=["knowledge-asset-governance"])

    async def authorize(request: Request, asset_id: str, *, manage: bool = True) -> KnowledgePrincipal:
        principal = await resolve_principal(request)
        if not isinstance(principal, KnowledgePrincipal):
            raise HTTPException(401, "authenticated knowledge principal required")
        asset = await service.assets.get_asset(principal.tenant_id, asset_id)
        if asset is None or (asset.project_id is not None and asset.project_id not in principal.project_ids):
            raise HTTPException(404, "asset not found")
        allowed = "knowledge.manage" in principal.permissions or (
            asset.project_id is not None and asset.project_id in principal.manage_project_ids
        )
        if manage and not allowed:
            raise HTTPException(403, "knowledge asset governance permission denied")
        if not manage and not (allowed or "knowledge.read" in principal.permissions):
            raise HTTPException(403, "knowledge asset read permission denied")
        return principal

    @router.put("/{asset_id}/quality")
    async def assess_quality(asset_id: str, request: Request) -> dict[str, Any]:
        principal = await authorize(request, asset_id)
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(400, "JSON object required")
        try:
            scores = {QualityDimension(key): value for key, value in payload.get("scores", {}).items()}
            assessment = QualityAssessment(principal.tenant_id, asset_id, principal.user_id,
                scores, payload.get("evidence", {}))
            saved = await service.assess_quality(assessment)
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"assessment_id": saved.assessment_id, "score": saved.score,
                "scores": {key.value: value for key, value in saved.scores.items()},
                "findings": list(saved.findings), "evidence": dict(saved.evidence),
                "assessed_at": saved.assessed_at.isoformat()}

    @router.get("/{asset_id}/quality")
    async def get_quality(asset_id: str, request: Request) -> dict[str, Any]:
        principal = await authorize(request, asset_id, manage=False)
        item = await service.repository.get_quality(principal.tenant_id, asset_id)
        if item is None:
            raise HTTPException(404, "quality assessment not found")
        return {"assessment_id": item.assessment_id, "score": item.score,
                "scores": {key.value: value for key, value in item.scores.items()},
                "findings": list(item.findings), "evidence": dict(item.evidence),
                "assessed_at": item.assessed_at.isoformat()}

    @router.put("/{asset_id}/freshness")
    async def set_freshness(asset_id: str, request: Request) -> dict[str, Any]:
        principal = await authorize(request, asset_id)
        payload = await request.json()
        try:
            policy = FreshnessPolicy(principal.tenant_id, asset_id,
                datetime.fromisoformat(payload["valid_from"]),
                datetime.fromisoformat(payload["expires_at"]),
                payload["review_interval_days"], datetime.fromisoformat(payload["last_reviewed_at"]))
            saved = await service.set_freshness(policy)
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"policy_id": saved.policy_id, "state": saved.state_at(datetime.now().astimezone()).value,
                "valid_from": saved.valid_from.isoformat(), "expires_at": saved.expires_at.isoformat(),
                "review_interval_days": saved.review_interval_days,
                "last_reviewed_at": saved.last_reviewed_at.isoformat()}

    @router.post("/{asset_id}/review")
    async def review(asset_id: str, request: Request) -> dict[str, Any]:
        principal = await authorize(request, asset_id)
        payload = await request.json()
        try:
            updated = await service.review(principal.tenant_id, asset_id,
                datetime.fromisoformat(payload["reviewed_at"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"policy_id": updated.policy_id, "last_reviewed_at": updated.last_reviewed_at.isoformat()}

    @router.get("/{asset_id}/freshness")
    async def get_freshness(asset_id: str, request: Request) -> dict[str, Any]:
        principal = await authorize(request, asset_id, manage=False)
        try:
            policy = await service.repository.get_freshness(principal.tenant_id, asset_id)
            state = await service.freshness_state(principal.tenant_id, asset_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"policy_id": policy.policy_id, "state": state.value,
                "valid_from": policy.valid_from.isoformat(), "expires_at": policy.expires_at.isoformat(),
                "review_interval_days": policy.review_interval_days,
                "last_reviewed_at": policy.last_reviewed_at.isoformat()}

    @router.put("/{asset_id}/source-permission")
    async def set_source_permission(asset_id: str, request: Request) -> dict[str, Any]:
        principal = await authorize(request, asset_id)
        payload = await request.json()
        try:
            permission = SourcePermission(principal.tenant_id, asset_id, payload["source_system"],
                payload["source_grant_id"], frozenset(payload.get("principals", [])))
            await service.set_source_permission(permission)
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"asset_id": asset_id, "source_system": permission.source_system,
                "inherited_principals": sorted(permission.inherited_principals), "active": permission.active}

    @router.post("/{asset_id}/source-permission/revoke")
    async def revoke_source_permission(asset_id: str, request: Request) -> dict[str, Any]:
        principal = await authorize(request, asset_id)
        try:
            receipts = await service.revoke_source_permission(principal.tenant_id, asset_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"asset_id": asset_id, "revoked": True, "receipts": [
            {"receipt_id": item.receipt_id, "target": item.target, "state": item.state.value,
             "requested_at": item.requested_at.isoformat(),
             "completed_at": item.completed_at.isoformat() if item.completed_at else None,
             "error": item.error} for item in receipts
        ]}

    return router
