"""Deterministic tenant-aware routing for gradual legacy-to-platform cutover.

The host gateway supplies an authenticated tenant resolver. This module never
trusts a tenant identifier from an arbitrary request header or query parameter.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

ASGIApp = Callable[[dict[str, Any], Callable[..., Awaitable[None]], Callable[..., Awaitable[None]]], Awaitable[None]]
TenantResolver = Callable[[dict[str, Any]], Awaitable[str | None]]


@dataclass(frozen=True)
class TenantCutoverPolicy:
    """Immutable routing policy shared by API and Workflow entry points."""

    enabled: bool
    tenant_allowlist: frozenset[str]
    traffic_percentage: int
    salt: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("enabled must be a boolean")
        if not isinstance(self.tenant_allowlist, frozenset):
            raise ValueError("tenant_allowlist must be a frozenset")
        if any(not isinstance(value, str) or not value.strip() for value in self.tenant_allowlist):
            raise ValueError("tenant allowlist entries must be non-empty strings")
        if (isinstance(self.traffic_percentage, bool)
                or not isinstance(self.traffic_percentage, int)
                or not 0 <= self.traffic_percentage <= 100):
            raise ValueError("traffic_percentage must be an integer between 0 and 100")
        if not isinstance(self.salt, bytes) or len(self.salt) < 16:
            raise ValueError("cutover salt must contain at least 16 bytes")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "TenantCutoverPolicy":
        """Load explicit deployment settings; malformed values fail startup."""
        values = os.environ if environ is None else environ
        enabled_raw = values.get("AGENT_PLATFORM_CUTOVER_ENABLED", "false").strip().lower()
        if enabled_raw not in {"true", "false"}:
            raise ValueError("AGENT_PLATFORM_CUTOVER_ENABLED must be true or false")
        percentage_raw = values.get("AGENT_PLATFORM_CUTOVER_PERCENTAGE", "0").strip()
        try:
            percentage = int(percentage_raw)
        except ValueError as exc:
            raise ValueError("AGENT_PLATFORM_CUTOVER_PERCENTAGE must be an integer") from exc
        allowlist = frozenset(
            item.strip() for item in values.get("AGENT_PLATFORM_CUTOVER_TENANTS", "").split(",")
            if item.strip()
        )
        salt_raw = values.get("AGENT_PLATFORM_CUTOVER_SALT", "")
        if not salt_raw and percentage:
            raise ValueError("AGENT_PLATFORM_CUTOVER_SALT is required when percentage is non-zero")
        salt = salt_raw.encode("utf-8") if salt_raw else b"disabled-cutover-salt"
        return cls(
            enabled=enabled_raw == "true",
            tenant_allowlist=allowlist,
            traffic_percentage=percentage,
            salt=salt,
        )

    def use_new_system(self, tenant_id: str | None) -> bool:
        """Return a stable decision for a trusted tenant identity."""
        if not self.enabled or not isinstance(tenant_id, str) or not tenant_id.strip():
            return False
        if tenant_id in self.tenant_allowlist:
            return True
        if self.traffic_percentage == 0:
            return False
        if self.traffic_percentage == 100:
            return True
        digest = hmac.new(self.salt, tenant_id.encode("utf-8"), hashlib.sha256).digest()
        bucket = int.from_bytes(digest[:8], "big") % 10_000
        return bucket < self.traffic_percentage * 100


def select_workflow_runtime(
    tenant_id: str | None,
    policy: TenantCutoverPolicy,
    *,
    new_runtime: Any,
    legacy_runtime: Any,
) -> Any:
    """Select the Workflow runtime using the same cohort policy as the API gateway."""
    return new_runtime if policy.use_new_system(tenant_id) else legacy_runtime


class CutoverGateway:
    """ASGI dispatcher that preserves the legacy app as the non-canary fallback.

    The tenant resolver must derive identity from authenticated host state (or
    independently validate credentials); request-supplied tenant headers are
    deliberately ignored by this class.
    """

    def __init__(
        self,
        *,
        new_app: ASGIApp,
        legacy_app: ASGIApp,
        policy: TenantCutoverPolicy,
        tenant_resolver: TenantResolver,
    ) -> None:
        if not callable(tenant_resolver):
            raise ValueError("an authenticated tenant_resolver is required")
        self.new_app = new_app
        self.legacy_app = legacy_app
        self.policy = policy
        self.tenant_resolver = tenant_resolver

    def rollback_to_legacy(self) -> None:
        """Atomically route new requests to legacy without changing app instances.

        This is a process-local routing switch. Multi-worker deployments must
        fan out the same disabled policy through their shared control plane and
        verify every worker before declaring rollback complete.
        """
        self.policy = TenantCutoverPolicy(
            enabled=False,
            tenant_allowlist=frozenset(),
            traffic_percentage=0,
            salt=self.policy.salt,
        )

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Awaitable[dict[str, Any]]],
                       send: Callable[..., Awaitable[None]]) -> None:
        if scope.get("type") not in {"http", "websocket"}:
            await self.new_app(scope, receive, send)
            return
        try:
            tenant_id = await self.tenant_resolver(scope)
        except Exception:
            await self._unavailable(send)
            return
        target = self.new_app if self.policy.use_new_system(tenant_id) else self.legacy_app
        await target(scope, receive, send)

    @staticmethod
    async def _unavailable(send: Callable[..., Awaitable[None]]) -> None:
        body = json.dumps({
            "error": {
                "code": "CUTOVER_IDENTITY_UNAVAILABLE",
                "message": "authenticated tenant context is temporarily unavailable",
            }
        }).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 503,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        })
        await send({"type": "http.response.body", "body": body})
