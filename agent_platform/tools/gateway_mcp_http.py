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
        loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        secure_transport = parsed.scheme == "https" or (
            parsed.scheme == "http" and loopback
        )
        if not secure_transport or not parsed.hostname:
            raise ValueError("resource server URL must be HTTPS except for loopback development")
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
    bearer_token: str | None = None,
    issuer_url: str,
    resource_server_url: str,
    expires_at: datetime | None = None,
    token_service: Any | None = None,
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
    resource_loopback = parsed_resource.hostname in {"127.0.0.1", "localhost", "::1"}
    resource_transport_ok = parsed_resource.scheme == "https" or (
        parsed_resource.scheme == "http" and resource_loopback
    )
    if (
        not resource_transport_ok
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
    verifier: Any
    if token_service is not None:
        from agent_platform.control_plane.gateway_auth import PersistentRunTokenVerifier
        verifier = PersistentRunTokenVerifier(
            token_service=token_service,
            resource_server_url=resource_server_url,
            expected_tenant_id=bridge.session.tenant_id,
            expected_run_id=bridge.session.run_id,
            expected_agent_id=bridge.session.agent_id,
            expected_actor_id=bridge.session.actor_id,
        )
    else:
        if bearer_token is None:
            raise ValueError("bearer_token is required when token_service is not provided")
        if expires_at is None:
            raise ValueError("expires_at is required when using a local bearer token")
        verifier = RunBoundTokenVerifier(
            bearer_token=bearer_token,
            actor_id=bridge.session.actor_id,
            resource_server_url=resource_server_url,
            expires_at=expires_at,
        )
    from agent_platform.tools.gateway_mcp import create_mcp_server

    try:
        from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
        from mcp.server.auth.middleware.bearer_auth import (
            BearerAuthBackend,
            RequireAuthMiddleware,
        )
        from mcp.server.auth.routes import (
            build_resource_metadata_url,
            create_protected_resource_routes,
        )
        from mcp.server.fastmcp.server import StreamableHTTPASGIApp
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
        from starlette.applications import Starlette
        from starlette.middleware import Middleware
        from starlette.middleware.authentication import AuthenticationMiddleware
        from starlette.routing import Route
    except ImportError as exc:
        raise RuntimeError("Install the MCP SDK Streamable HTTP dependencies") from exc

    server = create_mcp_server(bridge=bridge)
    # mcp 1.x 的底层 Server 只做协议处理；Streamable HTTP 的 ASGI 承载由
    # SessionManager + Starlette 组装，FastMCP 内部也是同一套组件。
    session_manager = StreamableHTTPSessionManager(
        app=server,
        json_response=True,
        stateless=False,
        security_settings=transport_security,
    )
    handler = StreamableHTTPASGIApp(session_manager)
    resource_url = url_adapter.validate_python(resource_server_url)
    required_scopes = list(auth.required_scopes or [])
    routes = [
        Route(
            parsed_resource.path,
            endpoint=RequireAuthMiddleware(
                handler,
                required_scopes,
                build_resource_metadata_url(resource_url),
            ),
        )
    ]
    routes.extend(
        create_protected_resource_routes(
            resource_url=resource_url,
            authorization_servers=[url_adapter.validate_python(issuer_url)],
            scopes_supported=required_scopes,
        )
    )
    app = Starlette(
        routes=routes,
        middleware=[
            Middleware(
                AuthenticationMiddleware,
                backend=BearerAuthBackend(
                    verifier, resource_server_url=resource_url
                ),
            ),
            Middleware(AuthContextMiddleware),
        ],
        lifespan=lambda _: session_manager.run(),
    )
    # The hosting control plane can revoke the run token during shutdown or
    # incident response without exposing token material to the MCP client.
    app.state.gateway_token_verifier = verifier
    return app
