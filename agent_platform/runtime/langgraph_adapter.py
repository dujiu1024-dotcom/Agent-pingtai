"""LangGraph adapter with an explicit Gateway-only contract for tool nodes."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Hashable, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any, Protocol
from uuid import uuid4


class ResolvedToolGateway(Protocol):
    async def call_resolved(
        self, *, tenant_id: str, actor_id: str, actor_roles: tuple[str, ...],
        tool_name: str, arguments: Mapping[str, Any], approval_id: str | None = None,
        run_id: str | None = None, agent_id: str | None = None,
        call_id: str | None = None, correlation_id: str | None = None,
    ) -> Any: ...


class GatewayToolClient:
    """Restricted per-node tool facade; identity comes from trusted runtime context."""

    def __init__(
        self, *, gateway: ResolvedToolGateway, allowed_tools: Sequence[str],
        identity: Mapping[str, Any], run_id: str, correlation_id: str | None = None,
    ) -> None:
        self._gateway = gateway
        self._allowed_tools = frozenset(allowed_tools)
        self._identity = dict(identity)
        self._run_id = run_id
        self._correlation_id = correlation_id

    async def call(self, tool_name: str, arguments: Mapping[str, Any]) -> Any:
        if tool_name not in self._allowed_tools:
            raise PermissionError("tool is not allowed for this LangGraph node")
        required = ("tenant_id", "actor_id", "actor_roles")
        if any(key not in self._identity for key in required):
            raise PermissionError("trusted tool identity is incomplete")
        roles = self._identity["actor_roles"]
        if isinstance(roles, str) or not isinstance(roles, Sequence):
            raise PermissionError("trusted tool roles must be a sequence")
        return await self._gateway.call_resolved(
            tenant_id=str(self._identity["tenant_id"]),
            actor_id=str(self._identity["actor_id"]),
            actor_roles=tuple(roles),
            tool_name=tool_name, arguments=arguments, run_id=self._run_id,
            agent_id=self._identity.get("agent_id"),
            approval_id=self._identity.get("approval_id"),
            call_id=f"lg-{uuid4().hex}",
            correlation_id=self._correlation_id,
        )


class GatewayToolNode:
    """Trusted LangGraph node wrapper exposing only its allowlisted Gateway facade."""

    __platform_gateway_only__ = True

    def __init__(
        self, *, handler: Callable[[Any, GatewayToolClient], Awaitable[Any] | Any],
        gateway: ResolvedToolGateway, allowed_tools: Sequence[str],
        identity_resolver: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    ) -> None:
        if not allowed_tools or len(set(allowed_tools)) != len(allowed_tools):
            raise ValueError("Gateway tool nodes require a unique non-empty tool allowlist")
        self.handler = handler
        self.gateway = gateway
        self.allowed_tools = tuple(allowed_tools)
        self.identity_resolver = identity_resolver

    async def __call__(self, state: Any, config: Mapping[str, Any]) -> Any:
        configurable = config.get("configurable", {})
        if not isinstance(configurable, Mapping):
            raise PermissionError("LangGraph runtime context is invalid")
        tenant_id = configurable.get("platform_tenant_id")
        run_id = configurable.get("platform_run_id")
        if not isinstance(tenant_id, str) or not tenant_id or not isinstance(run_id, str) or not run_id:
            raise PermissionError("tenant and Run identity are required for Gateway tool nodes")
        identity = self.identity_resolver(configurable)
        if not isinstance(identity, Mapping):
            raise PermissionError("trusted tool identity is invalid")
        if identity.get("tenant_id") != tenant_id:
            raise PermissionError("tool identity tenant does not match the active Run")
        actor_id = identity.get("actor_id")
        actor_roles = identity.get("actor_roles")
        if not isinstance(actor_id, str) or not actor_id.strip():
            raise PermissionError("trusted tool actor identity is required")
        if isinstance(actor_roles, str) or not isinstance(actor_roles, Sequence):
            raise PermissionError("trusted tool roles must be a sequence")
        client = GatewayToolClient(
            gateway=self.gateway, allowed_tools=self.allowed_tools, identity=identity,
            run_id=run_id, correlation_id=str(configurable.get("platform_correlation_id") or ""),
        )
        result = self.handler(state, client)
        if hasattr(result, "__await__"):
            return await result
        return result


def build_state_graph(
    *,
    state_schema: type,
    nodes: Mapping[str, Callable[..., Any]],
    edges: Sequence[tuple[str, str]] = (),
    conditional_edges: Sequence[tuple[str, Callable[..., str], Mapping[str, str]]] = (),
    entry_point: str | None = None,
    checkpointer: Any = None,
    tool_using_nodes: Sequence[str],
) -> Any:
    """Build a graph; explicitly declared tool nodes must use GatewayToolNode.

    Callers must explicitly inventory tool-using nodes, including an empty tuple
    when the graph is asserted to contain none. Plain callables are trusted platform
    logic, not an agent tool security boundary; they must never execute untrusted
    agent code or unreviewed external side effects.
    """
    missing = set(tool_using_nodes) - set(nodes)
    if missing:
        raise ValueError("declared tool nodes are not registered: " + ", ".join(sorted(missing)))
    invalid = [
        name for name in tool_using_nodes
        if not isinstance(nodes[name], GatewayToolNode)
    ]
    if invalid:
        raise ValueError(
            "tool-using LangGraph nodes must use GatewayToolNode: "
            + ", ".join(sorted(invalid))
        )
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:
        raise RuntimeError(
            "LangGraph is not installed. Install the platform runtime dependencies."
        ) from exc

    graph: Any = StateGraph(state_schema)
    for name, node in nodes.items():
        graph.add_node(name, node)

    if entry_point:
        graph.add_edge(START, entry_point)
    for source, target in edges:
        graph.add_edge(source, END if target == "__end__" else target)
    for source, router, destinations in conditional_edges:
        normalized: dict[Hashable, str] = {
            key: END if value == "__end__" else value
            for key, value in destinations.items()
        }
        graph.add_conditional_edges(source, router, normalized)

    return graph.compile(checkpointer=checkpointer)


@asynccontextmanager
async def postgres_checkpointer(dsn: str) -> AsyncIterator[Any]:
    """Yield a LangGraph PostgreSQL checkpointer with its schema initialized."""
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    except ImportError as exc:
        raise RuntimeError(
            "Install langgraph-checkpoint-postgres to use durable workflow checkpoints"
        ) from exc

    async with AsyncPostgresSaver.from_conn_string(dsn) as checkpointer:
        await checkpointer.setup()
        yield checkpointer
