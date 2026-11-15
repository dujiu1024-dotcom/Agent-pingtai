"""Authenticated Streamable HTTP hosting for a single Gateway MCP run."""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit


class RunBoundTokenVerifier:
    """Verify one short-lived opaque token bound to one Gateway MCP run.

    Tokens are minted by the trusted platform control plane. This verifier is
    intentionally per-server/per-run; it does not issue tokens or accept
    identity claims from request parameters.
    """

    def __init__(
        self,
        *,
        bearer_token: str,
        actor_id: str,
        resource_server_url: str,
        expires_at: datetime,
    ) -> None:
        if not bearer_token.strip():
            raise ValueError("bearer token must not be empty")
        if not actor_id.strip():
            raise ValueError("actor_id must not be empty")
        parsed = urlsplit(resource_server_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("resource server URL must be HTTPS")
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise ValueError("expires_at must be timezone-aware")
        self._token_digest = hashlib.sha256(bearer_token.encode("utf-8")).digest()
        self.actor_id = actor_id
        self.resource_server_url = resource_server_url
        self.expires_at = expires_at
        self._revoked = False

    def revoke(self) -> None:
        """Immediately reject further requests using this run token."""
        self._revoked = True

    async def verify_token(self, token: str) -> Any | None:
        """Return the MCP SDK AccessToken only while this run token is valid."""
        candidate = hashlib.sha256(token.encode("utf-8")).digest()
        if self._revoked or datetime.now(timezone.utc) >= self.expires_at:
            return None
        if not hmac.compare_digest(candidate, self._token_digest):
            return None

        try:
            from mcp.server.auth.provider import AccessToken
        except ImportError as exc:
            raise RuntimeError("MCP SDK auth support is required for HTTP hosting") from exc

        return AccessToken(
            token=token,
            client_id=self.actor_id,
            scopes=["platform:tools"],
            resource=self.resource_server_url,
            subject=self.actor_id,
        )


def create_authenticated_mcp_asgi_app(
    *,
    bridge: Any,
    bearer_token: str,
    issuer_url: str,
    resource_server_url: str,
    expires_at: datetime,
    host: str = "127.0.0.1",
    transport_security: Any | None = None,
) -> Any:
    """Build an authenticated per-run MCP Streamable HTTP ASGI application.

    A non-loopback bind requires explicit DNS-rebinding/Host/Origin policy.
    TLS termination and ingress network policy remain deployment concerns.
    """
    if host not in {"127.0.0.1", "localhost", "::1"} and transport_security is None:
        raise ValueError("non-loopback MCP hosting requires explicit transport_security")

    parsed_issuer = urlsplit(issuer_url)
    parsed_resource = urlsplit(resource_server_url)
    if (
        parsed_issuer.scheme != "https"
        or not parsed_issuer.hostname
        or parsed_issuer.username is not None
        or parsed_issuer.password is not None
        or parsed_issuer.query
        or parsed_issuer.fragment
    ):
        raise ValueError("issuer_url must be HTTPS without userinfo, query, or fragment")
    if (
        parsed_resource.scheme != "https"
        or not parsed_resource.hostname
        or parsed_resource.username is not None
        or parsed_resource.password is not None
        or parsed_resource.query
        or parsed_resource.fragment
        or not parsed_resource.path
    ):
        raise ValueError(
            "resource_server_url must be an HTTPS endpoint URL without userinfo, query, or fragment"
        )

    try:
        from mcp.server.auth.settings import AuthSettings
        from pydantic import AnyHttpUrl, TypeAdapter
    except ImportError as exc:
        raise RuntimeError("Install MCP SDK auth dependencies for HTTP hosting") from exc

    url_adapter = TypeAdapter(AnyHttpUrl)
    auth = AuthSettings(
        issuer_url=url_adapter.validate_python(issuer_url),
        resource_server_url=url_adapter.validate_python(resource_server_url),
        required_scopes=["platform:tools"],
        validate_token_resource=True,
    )
    verifier = RunBoundTokenVerifier(
        bearer_token=bearer_token,
        actor_id=bridge.session.actor_id,
        resource_server_url=resource_server_url,
        expires_at=expires_at,
    )
    from agent_platform.tools.gateway_mcp import create_mcp_server

    server = create_mcp_server(bridge=bridge)
    return server.streamable_http_app(
        auth=auth,
        token_verifier=verifier,
        host=host,
        transport_security=transport_security,
        stateless_http=False,
        json_response=True,
        streamable_http_path=parsed_resource.path,
    )
