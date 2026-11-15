"""Run-scoped Strands MCP client for the platform Tool Gateway.

The endpoint resolver is a trusted control-plane port. It must provision an
endpoint whose server-side session is already bound to the supplied RunContext
and effective tool allowlist. The model never receives endpoint credentials.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from agent_platform.contracts import RunContext


@dataclass(frozen=True)
class GatewayMCPEndpoint:
    """Short-lived authenticated endpoint for one trusted Agent Run."""

    url: str
    bearer_token: str

    def __post_init__(self) -> None:
        parsed = urlsplit(self.url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "Gateway MCP endpoint must be an HTTPS URL without userinfo, query, or fragment"
            )
        if not self.bearer_token.strip():
            raise ValueError("Gateway MCP endpoint bearer token must not be empty")


EndpointResolver = Callable[
    [RunContext, Sequence[str]], GatewayMCPEndpoint
]
MCPClientFactory = Callable[..., Any]


async def _run_blocking_cleanup_safe(function: Callable[..., Any], *args: Any) -> Any:
    """Run a blocking SDK call without abandoning it when the caller is cancelled."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        except Exception:
            pass
        raise


def _default_mcp_client_factory(**kwargs: Any) -> Any:
    try:
        from strands.tools.mcp import MCPClient
    except ImportError as exc:
        raise RuntimeError(
            "Install requirements-harness-strands.txt with Strands MCP support"
        ) from exc
    return MCPClient(**kwargs)


class StrandsMCPGatewayToolProvider:
    """Connect Strands to a per-run, authenticated Gateway MCP endpoint.

    The endpoint resolver must return a one-run URL and a short-lived bearer
    token. Server-side authentication maps that token to immutable tenant,
    actor, role, run, agent, approval, and effective-tool context.
    """

    def __init__(
        self,
        *,
        endpoint_resolver: EndpointResolver,
        client_factory: MCPClientFactory | None = None,
        startup_timeout_seconds: int = 10,
    ) -> None:
        if startup_timeout_seconds < 1:
            raise ValueError("startup_timeout_seconds must be positive")
        self.endpoint_resolver = endpoint_resolver
        self.client_factory = client_factory or _default_mcp_client_factory
        self.startup_timeout_seconds = startup_timeout_seconds

    @asynccontextmanager
    async def open_session(
        self, *, context: RunContext, allowed_tools: Sequence[str]
    ) -> AsyncIterator[Sequence[Any]]:
        names = tuple(allowed_tools)
        if not names or any(not name.strip() for name in names):
            raise ValueError("Gateway MCP session requires non-empty allowed tool names")
        if len(names) != len(set(names)):
            raise ValueError("Gateway MCP allowed tool names must be unique")

        endpoint = self.endpoint_resolver(context, names)
        if not isinstance(endpoint, GatewayMCPEndpoint):
            raise TypeError("endpoint resolver must return GatewayMCPEndpoint")

        client = self.client_factory(
            url=endpoint.url,
            headers={"Authorization": f"Bearer {endpoint.bearer_token}"},
            tool_filters={"allowed": list(names)},
            startup_timeout=self.startup_timeout_seconds,
            application_name="agent-platform-strands",
            continue_on_error=False,
        )
        entered = False
        try:
            # Set before entering: MCPClient.stop() is defensive after partial startup.
            entered = True
            await _run_blocking_cleanup_safe(client.__enter__)
            tools = await _run_blocking_cleanup_safe(client.list_tools_sync)
            discovered = tuple(getattr(tool, "tool_name", None) for tool in tools)
            if any(not name for name in discovered):
                raise RuntimeError("Strands MCP client returned a tool without a name")
            if set(discovered) != set(names) or len(discovered) != len(names):
                raise RuntimeError(
                    "Gateway MCP tool discovery does not exactly match the run allowlist"
                )
            yield tuple(tools)
        finally:
            if entered:
                await _run_blocking_cleanup_safe(client.__exit__, None, None, None)
