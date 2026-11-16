from __future__ import annotations

import unittest
from datetime import datetime, timezone

from agent_platform.approval.policy import RiskLevel
from agent_platform.control_plane.registry import RegistryError, RegistryVersion
from agent_platform.tools.gateway import ToolDefinition
from agent_platform.tools.registry_catalog import build_tool_definitions


class ToolRegistryCatalogTests(unittest.TestCase):
    def setUp(self):
        async def handler(arguments):
            return {"ok": True}
        self.handler = handler
        async def authorize(_context, _arguments):
            return True
        self.trusted = ToolDefinition(
            name="send-email", risk=RiskLevel.READ_ONLY, handler=handler,
            description="Host-registered email handler",
            resource_resolver=lambda arguments: f"email:{arguments.get('to', 'global')}",
            resource_authorizer=authorize,
        )

    def registry_tool(self, definition, *, status="published"):
        return RegistryVersion(
            tenant_id="tenant-a", kind="tool", item_id="send-email", version=3,
            definition=definition, status=status, created_by="admin",
            created_at=datetime.now(timezone.utc),
        )

    def test_risk_and_schema_are_loaded_from_registry_but_handler_is_trusted(self):
        tools = build_tool_definitions(
            published_tools=(self.registry_tool({
                "risk_level": "external_or_irreversible",
                "description": "Send an approved email",
                "allowed_roles": ["agent"],
                "input_schema": {
                    "type": "object",
                    "properties": {"to": {"type": "string"}},
                    "required": ["to"],
                    "additionalProperties": False,
                },
            }),),
            trusted_handlers={"send-email": self.trusted},
        )
        self.assertEqual(tools[0].risk, RiskLevel.EXTERNAL_OR_IRREVERSIBLE)
        self.assertIs(tools[0].handler, self.handler)
        self.assertEqual(tools[0].allowed_roles, ("agent",))

    def test_missing_trusted_handler_or_invalid_schema_is_rejected(self):
        with self.assertRaises(RegistryError):
            build_tool_definitions(
                published_tools=(self.registry_tool({
                    "risk_level": "read_only", "input_schema": {"type": "object"},
                }),),
                trusted_handlers={},
            )
        with self.assertRaises(RegistryError):
            build_tool_definitions(
                published_tools=(self.registry_tool({
                    "risk_level": "read_only", "input_schema": {"type": "not-a-schema"},
                }),),
                trusted_handlers={"send-email": self.trusted},
            )

    def test_missing_resource_acl_callbacks_are_rejected(self):
        unscoped = ToolDefinition(
            name="send-email", risk=RiskLevel.READ_ONLY, handler=self.handler,
        )
        with self.assertRaisesRegex(RegistryError, "resource_resolver and resource_authorizer"):
            build_tool_definitions(
                published_tools=(self.registry_tool({
                    "risk_level": "read_only",
                    "input_schema": {"type": "object"},
                }),),
                trusted_handlers={"send-email": unscoped},
            )


if __name__ == "__main__":
    unittest.main()
