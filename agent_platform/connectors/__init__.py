"""Shared connector contracts and authentication adapters."""

from agent_platform.connectors.base import (
    BaseConnector,
    ConnectorAuthenticationError,
    ConnectorAuthenticator,
    ConnectorAuthRequest,
    SecretStoreConnectorAuthenticator,
)

__all__ = [
    "BaseConnector",
    "ConnectorAuthRequest",
    "ConnectorAuthenticationError",
    "ConnectorAuthenticator",
    "SecretStoreConnectorAuthenticator",
]
