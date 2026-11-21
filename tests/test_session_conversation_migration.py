import unittest
from pathlib import Path


class SessionConversationMigrationTests(unittest.TestCase):
    def test_migration_has_complete_token_usage_constraint(self):
        sql = Path(
            "agent_platform/storage/postgres/082_session_conversation_context.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("platform_messages_token_usage_check", sql)
        self.assertIn("token_usage->>'prompt_tokens'", sql)
        self.assertIn("token_usage->>'completion_tokens'", sql)
        self.assertIn("token_usage->>'total_tokens'", sql)
        self.assertIn("ELSE false", sql)
        self.assertNotIn("COMMIT;\n, false)", sql)

    def test_context_snapshot_is_tenant_and_session_scoped(self):
        sql = Path(
            "agent_platform/storage/postgres/082_session_conversation_context.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("PRIMARY KEY (tenant_id, snapshot_id)", sql)
        self.assertIn("UNIQUE (tenant_id, session_id, version)", sql)
        self.assertIn(
            "FOREIGN KEY (tenant_id, last_message_id, session_id)", sql
        )
        self.assertIn(
            "REFERENCES platform_messages (tenant_id, message_id, session_id)",
            sql,
        )


if __name__ == "__main__":
    unittest.main()
