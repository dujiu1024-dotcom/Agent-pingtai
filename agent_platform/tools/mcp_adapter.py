"""Thin MCP session adapter for the supervised Tool Gateway."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any


class MCPToolCallError(RuntimeError):
    """Raised when a remote MCP server reports a tool execution error."""


def make_mcp_handler(
    session: Any, *, remote_tool_name: str
) -> Callable[[Mapping[str, Any]], Awaitable[Any]]:
    """Bind a trusted MCP session and tool name to a platform ToolDefinition.

    The model never selects the remote server or tool name. The platform's
    trusted ToolDefinition fixes that mapping and supplies the risk level.
    """
    if not remote_tool_name.strip():
        raise ValueError("remote_tool_name must not be empty")

    async def handler(arguments: Mapping[str, Any]) -> Any:
        result = await session.call_tool(
            remote_tool_name,
            arguments=dict(arguments),
        )
        if getattr(result, "isError", False):
            raise MCPToolCallError("MCP server returned an execution error")
        return result

    return handler
