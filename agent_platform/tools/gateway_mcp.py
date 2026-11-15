"""Per-run MCP tool surface backed exclusively by the platform Tool Gateway.

Create one bridge/server per trusted run context. Never share a bridge between
tenants or let model-controlled arguments supply identity, roles, approvals,
resource names, remote server names, or risk classifications.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from agent_platform.tools.gateway import (
    SupervisedToolGateway,
    ToolExecutionDenied,
)


@dataclass(frozen=True)
class GatewaySessionContext:
    tenant_id: str
    actor_id: str
    actor_roles: tuple[str, ...]
    run_id: str
    agent_id: str
    allowed_tool_names: tuple[str, ...]
    approval_ids: Mapping[str, str]

    def __post_init__(self) -> None:
        for field_name in ("tenant_id", "actor_id", "run_id", "agent_id"):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must not be empty")
        if len(self.allowed_tool_names) != len(set(self.allowed_tool_names)):
            raise ValueError("allowed tool names must be unique")
        if any(not name.strip() for name in self.allowed_tool_names):
            raise ValueError("allowed tool names must not be empty")
        if set(self.approval_ids) - set(self.allowed_tool_names):
            raise ValueError("approval IDs may only be bound to allowed tools")


class GatewayMCPBridge:
    """MCP list/call surface with identity and authority fixed by the host."""

    def __init__(
        self,
        *,
        gateway: SupervisedToolGateway,
        session: GatewaySessionContext,
    ) -> None:
        try:
            from jsonschema import Draft202012Validator
        except ImportError as exc:
            raise RuntimeError(
                "Install jsonschema to expose MCP tools with schema validation"
            ) from exc
        self._validator_class = Draft202012Validator
        self.gateway = gateway
        self.session = session
        definitions = {tool.name: tool for tool in gateway.registered_tools}
        missing = set(session.allowed_tool_names) - set(definitions)
        if missing:
            raise ValueError(
                "session references unregistered tools: " + ", ".join(sorted(missing))
            )

        self._definitions = {}
        for name in session.allowed_tool_names:
            definition = definitions[name]
            if definition.allowed_roles and not set(session.actor_roles).intersection(
                definition.allowed_roles
            ):
                continue
            if definition.input_schema is None or definition.resource_resolver is None:
                raise ValueError(
                    f"tool {name!r} needs a JSON Schema and trusted resource resolver "
                    "before MCP exposure"
                )
            schema = dict(definition.input_schema)
            if schema.get("type") != "object":
                raise ValueError(f"tool {name!r} input schema must be an object")
            if schema.get("additionalProperties", True) is not False:
                raise ValueError(
                    f"tool {name!r} input schema must set additionalProperties=false"
                )
            self._validator_class.check_schema(schema)
            self._definitions[name] = (definition, schema)

    def list_tools(self) -> list[dict[str, Any]]:
        """Return MCP-compatible descriptors for this run's authorized tools."""
        return [
            {
                "name": name,
                "description": definition.description,
                "inputSchema": schema,
            }
            for name, (definition, schema) in self._definitions.items()
        ]

    async def call_tool(
        self, *, name: str, arguments: Mapping[str, Any]
    ) -> Any:
        bound = self._definitions.get(name)
        if bound is None:
            raise ToolExecutionDenied("tool is not available in this run's MCP session")
        definition, schema = bound
        validation_errors = list(
            self._validator_class(schema).iter_errors(dict(arguments))
        )
        if validation_errors:
            raise ToolExecutionDenied(
                "tool arguments do not match the registered schema: "
                + validation_errors[0].message
            )

        resolver = definition.resource_resolver
        if resolver is None:
            raise ToolExecutionDenied("tool has no trusted resource resolver")
        try:
            resource = resolver(arguments)
        except Exception as exc:
            raise ToolExecutionDenied("tool resource could not be resolved") from exc
        if not isinstance(resource, str) or not resource.strip():
            raise ToolExecutionDenied("tool resource resolver returned an empty resource")

        return await self.gateway.call(
            tenant_id=self.session.tenant_id,
            actor_id=self.session.actor_id,
            actor_roles=self.session.actor_roles,
            tool_name=name,
            resource=resource,
            arguments=arguments,
            approval_id=self.session.approval_ids.get(name),
        )


def create_mcp_server(*, bridge: GatewayMCPBridge, name: str = "agent-platform-gateway"):
    """Create an MCP SDK server bound to exactly one trusted run session.

    The returned server has no independent tools or remote connections. Its
    list_tools and call_tool handlers delegate only to the bridge. The host is
    responsible for attaching a transport and keeping this server instance
    private to the associated run/tenant.
    """
    try:
        from mcp.server import Server
        from mcp.types import TextContent, Tool
    except ImportError as exc:
        raise RuntimeError("Install the platform MCP dependency to create this server") from exc

    server = Server(name)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name=item["name"],
                description=item["description"],
                inputSchema=item["inputSchema"],
            )
            for item in bridge.list_tools()
        ]

    @server.call_tool()
    async def call_tool(
        tool_name: str, arguments: dict[str, Any] | None
    ) -> list[TextContent]:
        result = await bridge.call_tool(name=tool_name, arguments=arguments or {})
        return [
            TextContent(
                type="text",
                text=json.dumps(result, ensure_ascii=False, default=str),
            )
        ]

    return server
