"""Build Gateway tool definitions from published tenant registry entries.

Executable handlers and authorization callbacks always come from trusted host
composition. Registry content can select metadata and risk classification only.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Mapping, Sequence

from agent_platform.approval.policy import RiskLevel
from agent_platform.control_plane.registry import RegistryError, RegistryVersion
from agent_platform.tools.gateway import ToolDefinition

_RISK_LEVELS = {
    "read_only": RiskLevel.READ_ONLY,
    "internal_reversible": RiskLevel.INTERNAL_REVERSIBLE,
    "external_or_irreversible": RiskLevel.EXTERNAL_OR_IRREVERSIBLE,
    "financial_or_security": RiskLevel.FINANCIAL_OR_SECURITY,
}


def build_tool_definitions(
    *, published_tools: Sequence[RegistryVersion],
    trusted_handlers: Mapping[str, ToolDefinition],
) -> tuple[ToolDefinition, ...]:
    """Resolve trusted handlers and registry-owned metadata into Gateway tools."""
    built: list[ToolDefinition] = []
    seen: set[str] = set()
    for item in published_tools:
        if item.kind != "tool" or item.status != "published":
            raise RegistryError("Gateway accepts only published tool registry versions")
        if item.item_id in seen:
            raise RegistryError("only one active version per tool may be loaded")
        seen.add(item.item_id)
        trusted = trusted_handlers.get(item.item_id)
        if trusted is None:
            raise RegistryError(f"no trusted handler is registered for tool {item.item_id}")
        definition = dict(item.definition)
        risk_name = definition.get("risk_level")
        risk = _RISK_LEVELS.get(risk_name) if isinstance(risk_name, str) else None
        if risk is None:
            raise RegistryError(f"tool {item.item_id} has an invalid risk_level")
        schema = definition.get("input_schema")
        if not isinstance(schema, dict):
            raise RegistryError(f"tool {item.item_id} must declare a JSON Schema object")
        roles = definition.get("allowed_roles", [])
        if not isinstance(roles, list) or any(not isinstance(role, str) or not role.strip() for role in roles):
            raise RegistryError(f"tool {item.item_id} allowed_roles must be a string array")
        try:
            from jsonschema import Draft202012Validator
            Draft202012Validator.check_schema(schema)
        except ImportError as exc:
            raise RuntimeError("jsonschema is required to load registered tools") from exc
        except Exception as exc:
            raise RegistryError(f"tool {item.item_id} has an invalid JSON Schema") from exc
        description = definition.get("description", trusted.description)
        if not isinstance(description, str):
            raise RegistryError(f"tool {item.item_id} description must be a string")
        built.append(replace(
            trusted, name=item.item_id, risk=risk, description=description,
            allowed_roles=tuple(sorted(set(role.strip() for role in roles))),
            input_schema=schema,
        ))
    return tuple(sorted(built, key=lambda tool: tool.name))
