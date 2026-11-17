"""Shared connector lifecycle and fail-closed credential authentication.

Connector implementations own provider-specific transport and data mapping. This
module centralizes credential resolution without retaining plaintext credentials.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit


class ConnectorAuthenticationError(RuntimeError):
    """Connector authentication failed or its credential reference is unsafe."""


@dataclass(frozen=True)
class ConnectorAuthRequest:
    tenant_id: str
    connector_id: str
    credential_ref: str
    required_scopes: tuple[str, ...]


class ConnectorAuthenticator(Protocol):
    async def authenticate(self, request: ConnectorAuthRequest) -> str: ...


class SecretStoreConnectorAuthenticator:
    """Adapter for the shared #27 SecretStore/CredentialService.

    The credential service enforces tenant, consumer grant, actor audit and scope
    subset checks before returning plaintext. This adapter only translates the
    opaque secret:// reference into that service's lookup arguments.
    """

    def __init__(
        self, *, secret_store: Any, consumer_type: str, consumer_id: str, actor_id: str,
    ) -> None:
        if consumer_type not in {"agent", "workflow"}:
            raise ValueError("consumer_type must be agent or workflow")
        if not consumer_id.strip() or not actor_id.strip():
            raise ValueError("consumer_id and actor_id are required")
        self.secret_store = secret_store
        self.consumer_type = consumer_type
        self.consumer_id = consumer_id
        self.actor_id = actor_id

    async def authenticate(self, request: ConnectorAuthRequest) -> str:
        parsed = urlsplit(request.credential_ref)
        parts = parsed.path.strip("/").split("/")
        if (
            parsed.scheme != "secret" or parsed.netloc != request.tenant_id
            or len(parts) != 2 or parts[0] != "credentials" or not parts[1]
            or parsed.query or parsed.fragment or not request.required_scopes
        ):
            raise ConnectorAuthenticationError("credential reference or required scopes are invalid")
        try:
            value = await self.secret_store.resolve(
                tenant_id=request.tenant_id,
                credential_id=parts[1],
                consumer_type=self.consumer_type,
                consumer_id=self.consumer_id,
                required_scopes=request.required_scopes,
                actor_id=self.actor_id,
            )
        except Exception as exc:
            # Do not expose provider errors, token values, or secret references.
            raise ConnectorAuthenticationError("connector credential resolution denied") from exc
        if not isinstance(value, str) or not value.strip():
            raise ConnectorAuthenticationError("connector credential is unavailable")
        return value


class BaseConnector:
    """Common connector lifecycle and authentication contract.

    Legacy resolvers remain accepted as a compatibility bridge. New integrations
    should pass SecretStoreConnectorAuthenticator so grants, scopes and audit are
    checked centrally for every operation.
    """

    def __init__(
        self, *, connector_id: str, credential_ref: str | None = None,
        credentials: Any | None = None,
        authenticator: ConnectorAuthenticator | None = None,
        auth_scopes: tuple[str, ...] = (),
    ) -> None:
        if not connector_id.strip():
            raise ValueError("connector_id is required")
        if credential_ref is not None and not credential_ref.startswith("secret://"):
            raise ValueError("connector credentials must use a secret:// reference")
        if authenticator is not None and credential_ref is None:
            raise ValueError("shared authentication requires a secret:// credential reference")
        if authenticator is not None and not auth_scopes:
            raise ValueError("shared authentication requires explicit auth_scopes")
        if len(set(auth_scopes)) != len(auth_scopes) or any(
            not isinstance(scope, str) or not scope.strip() for scope in auth_scopes
        ):
            raise ValueError("auth_scopes must contain unique non-empty scope tags")
        self.connector_id = connector_id
        self.credential_ref = credential_ref
        self.credentials = credentials
        self.authenticator = authenticator
        self.auth_scopes = tuple(auth_scopes)

    async def authenticate(
        self, *, tenant_id: str, required_scopes: tuple[str, ...] | None = None,
    ) -> str:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ConnectorAuthenticationError("tenant_id is required")
        if not self.credential_ref:
            raise ConnectorAuthenticationError("connector credential reference is not configured")
        scopes = self.auth_scopes if required_scopes is None else tuple(required_scopes)
        if self.authenticator is not None:
            token = await self.authenticator.authenticate(ConnectorAuthRequest(
                tenant_id=tenant_id, connector_id=self.connector_id,
                credential_ref=self.credential_ref, required_scopes=scopes,
            ))
        elif self.credentials is not None:
            # Compatibility path for existing injected secret resolvers. It never
            # stores the returned value; migration can be staged without downtime.
            token = await self.credentials.resolve(self.credential_ref)
        else:
            raise ConnectorAuthenticationError("connector authenticator is not configured")
        if not isinstance(token, str) or not token.strip():
            raise ConnectorAuthenticationError("connector credential is unavailable")
        return token

    async def start(self) -> None:
        """Lifecycle hook for catalog-managed connectors."""

    async def close(self) -> None:
        """Lifecycle hook for catalog-managed connectors."""
