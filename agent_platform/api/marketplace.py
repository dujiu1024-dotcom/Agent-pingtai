"""Tenant-authorized API for ecosystem marketplace governance."""
from __future__ import annotations

import inspect
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from agent_platform.identity.models import Principal


class Submission(BaseModel):
    publisher_id: str = Field(min_length=1, max_length=128)
    asset_id: str = Field(min_length=2, max_length=128)
    asset_kind: str
    version: str
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    digest: str
    signature: str
    visibility: str = "private"
    license_id: str = Field(min_length=1, max_length=128)


class Review(BaseModel):
    approved: bool
    reason: str = Field(min_length=1, max_length=2000)


class Trial(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)


class Rating(BaseModel):
    installation_id: str = Field(min_length=1, max_length=128)
    score: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=2000)


def create_marketplace_router(*, resolve_principal: Callable[[], Any],
                              resolve_service: Callable[[Principal], Any],
                              authorize: Callable[[Principal, str, str], Any]) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/marketplace", tags=["marketplace"])

    async def principal() -> Principal:
        value = resolve_principal()
        value = await value if inspect.isawaitable(value) else value
        if not isinstance(value, Principal):
            raise HTTPException(401, "authentication required")
        return value

    async def service(p: Principal) -> Any:
        value = resolve_service(p)
        value = await value if inspect.isawaitable(value) else value
        if value is None:
            raise HTTPException(503, "marketplace governance is not configured")
        return value

    async def permit(p: Principal, action: str, asset_id: str = "*") -> None:
        value = authorize(p, action, asset_id)
        value = await value if inspect.isawaitable(value) else value
        if value is not True:
            raise HTTPException(403, "marketplace operation denied")

    def translate(exc: Exception) -> None:
        from agent_platform.marketplace.governance import MarketplaceGateError
        if isinstance(exc, PermissionError):
            raise HTTPException(403, str(exc)) from exc
        if isinstance(exc, LookupError):
            raise HTTPException(404, str(exc)) from exc
        if isinstance(exc, (ValueError, MarketplaceGateError)):
            raise HTTPException(409, str(exc)) from exc
        raise HTTPException(503, "marketplace service unavailable") from exc

    @router.get("/assets")
    async def catalog(visibility: str | None = Query(default=None), p: Principal = Depends(principal)):
        await permit(p, "marketplace.read")
        try:
            return await (await service(p)).catalog(tenant_id=p.tenant_id, visibility=visibility)
        except Exception as exc:
            translate(exc)

    @router.post("/submissions", status_code=201)
    async def submit(body: Submission, p: Principal = Depends(principal)):
        await permit(p, f"marketplace.submit:{body.publisher_id}", body.asset_id)
        try:
            return await (await service(p)).submit(**body.model_dump(),
                tenant_id=p.tenant_id, actor_id=p.user_id)
        except Exception as exc:
            translate(exc)

    @router.post("/assets/{asset_id}/versions/{version}/review")
    async def review(asset_id: str, version: str, body: Review, p: Principal = Depends(principal)):
        await permit(p, "marketplace.review", asset_id)
        try:
            return await (await service(p)).review(tenant_id=p.tenant_id, asset_id=asset_id,
                version=version, reviewer_id=p.user_id, approved=body.approved, reason=body.reason)
        except Exception as exc:
            translate(exc)

    @router.post("/assets/{asset_id}/versions/{version}/trial")
    async def trial(asset_id: str, version: str, body: Trial, p: Principal = Depends(principal)):
        await permit(p, "marketplace.trial", asset_id)
        try:
            return await (await service(p)).trial(tenant_id=p.tenant_id, asset_id=asset_id,
                version=version, actor_id=p.user_id, trial_config=body.config)
        except Exception as exc:
            translate(exc)

    @router.post("/assets/{asset_id}/versions/{version}/publish")
    async def publish(asset_id: str, version: str, p: Principal = Depends(principal)):
        await permit(p, "marketplace.publish", asset_id)
        try:
            return await (await service(p)).publish(tenant_id=p.tenant_id, asset_id=asset_id,
                version=version, actor_id=p.user_id)
        except Exception as exc:
            translate(exc)

    @router.post("/assets/{asset_id}/versions/{version}/withdraw")
    async def withdraw(asset_id: str, version: str, body: Review, p: Principal = Depends(principal)):
        await permit(p, "marketplace.withdraw", asset_id)
        try:
            return await (await service(p)).withdraw(tenant_id=p.tenant_id, asset_id=asset_id,
                version=version, actor_id=p.user_id, reason=body.reason)
        except Exception as exc:
            translate(exc)

    @router.post("/assets/{asset_id}/versions/{version}/ratings", status_code=201)
    async def rate(asset_id: str, version: str, body: Rating, p: Principal = Depends(principal)):
        await permit(p, "marketplace.rate", asset_id)
        try:
            return await (await service(p)).rate(tenant_id=p.tenant_id, asset_id=asset_id,
                version=version, installation_id=body.installation_id, user_id=p.user_id,
                score=body.score, comment=body.comment)
        except Exception as exc:
            translate(exc)

    @router.get("/assets/{asset_id}/versions")
    async def versions(asset_id: str, p: Principal = Depends(principal)):
        await permit(p, "marketplace.read", asset_id)
        try:
            return await (await service(p)).versions(tenant_id=p.tenant_id, asset_id=asset_id)
        except Exception as exc:
            translate(exc)

    return router
