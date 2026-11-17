"""MCP SDK Bearer verifier backed by the shared RunTokenService."""
from __future__ import annotations

from typing import Any


class PersistentRunTokenVerifier:
    """Verify persistent credentials against the exact MCP session binding.

    The token store is authoritative for expiry and revocation. This adapter
    additionally prevents a valid token minted for one actor, tenant, Agent, or
    Run from being replayed against another per-Run MCP bridge.
    """

    def __init__(
        self,
        *,
        token_service: Any,
        resource_server_url: str,
        expected_tenant_id: str,
        expected_run_id: str,
        expected_agent_id: str,
        expected_actor_id: str,
    ) -> None:
        expected = {
            "tenant_id": expected_tenant_id,
            "run_id": expected_run_id,
            "agent_id": expected_agent_id,
            "actor_id": expected_actor_id,
        }
        if any(not isinstance(value, str) or not value.strip() for value in expected.values()):
            raise ValueError("expected Run token identity fields must be non-empty strings")
        self.token_service = token_service
        self.resource_server_url = resource_server_url
        self.expected_identity = expected

    async def verify_token(self, token: str) -> Any | None:
        record = await self.token_service.resolve(
            token, endpoint_url=self.resource_server_url
        )
        if record is None:
            return None
        if any(
            getattr(record, field_name, None) != expected_value
            for field_name, expected_value in self.expected_identity.items()
        ):
            return None
        if getattr(record, "endpoint_url", None) != self.resource_server_url:
            return None
        scopes = getattr(record, "scopes", ())
        if (
            not isinstance(scopes, (tuple, list))
            or not scopes
            or any(not isinstance(scope, str) or not scope.strip() for scope in scopes)
        ):
            return None

        try:
            from mcp.server.auth.provider import AccessToken
        except ImportError as exc:
            raise RuntimeError("MCP SDK auth support required") from exc
        return AccessToken(
            token=token,
            client_id=record.actor_id,
            scopes=list(scopes),
            resource=record.endpoint_url,
            subject=record.actor_id,
        )
