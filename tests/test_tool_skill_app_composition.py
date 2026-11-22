from __future__ import annotations

import unittest

from fastapi import FastAPI

from agent_platform.api.app import create_platform_app


class ToolSkillCompositionTests(unittest.TestCase):
    def test_evaluation_dependencies_are_required_as_a_complete_set(self):
        # The app factory's required core dependencies are deliberately represented
        # by inert sentinels; paired dependency validation runs before request handling.
        with self.assertRaisesRegex(ValueError, "Tool/MCP checker"):
            create_platform_app(
                auth_enabled=True,
                identity=object(),
                identity_repository=object(),
                knowledge=object(),
                memory=object(),
                redis=object(),
                allowed_origins=("https://console.example.com",),
                tool_mcp_checker_resolver=object(),
            )


if __name__ == "__main__":
    unittest.main()
