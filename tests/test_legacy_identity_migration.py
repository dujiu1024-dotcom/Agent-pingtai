"""Tests for safe, explicit legacy identity migration planning."""
import hashlib
import os
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from agent_platform.identity.legacy_migration import (
    build_legacy_migration_plan,
    write_mapping_template,
)


class LegacyIdentityMigrationPlanTests(unittest.TestCase):
    def _database(self, path: Path) -> None:
        with sqlite3.connect(path) as connection:
            connection.execute(
                """CREATE TABLE users (
                   open_id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL,
                   projects TEXT NOT NULL DEFAULT '', console_token TEXT,
                   created_at TEXT NOT NULL)"""
            )
            connection.executemany(
                "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)",
                [
                    ("wx-user-1", "Alice", "member", "p1,p2", "never-export", "2025-01-01"),
                    ("wx-user-2", "Bob", "viewer", "p2", None, "2025-01-02"),
                ],
            )

    def test_template_contains_only_hashed_legacy_refs_and_requires_manual_claims(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "legacy.sqlite"
            output = Path(directory) / "mapping.json"
            self._database(database)
            write_mapping_template(
                database, output, tenant_id="tenant-a", provider="wechat-work",
            )
            rendered = output.read_text(encoding="utf-8")
            self.assertNotIn("wx-user-1", rendered)
            self.assertNotIn("never-export", rendered)
            payload = json.loads(rendered)
            ref = hashlib.sha256(b"wx-user-1").hexdigest()[:16]
            self.assertEqual(payload["accounts"][ref]["user_id"], "")
            self.assertIn("member", payload["role_mappings"])

    def test_plan_requires_explicit_user_and_role_mappings(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "legacy.sqlite"
            mapping = Path(directory) / "mapping.json"
            self._database(database)
            mapping.write_text(json.dumps({
                "mapping_version": 1, "tenant_id": "tenant-a",
                "provider": "wechat-work",
                "role_mappings": {
                    "member": {"tenant_role_id": "member", "project_role_id": None},
                    "viewer": {"tenant_role_id": None, "project_role_id": None},
                },
                "accounts": {
                    hashlib.sha256(b"wx-user-1").hexdigest()[:16]: {"user_id": str(uuid4())},
                    hashlib.sha256(b"wx-user-2").hexdigest()[:16]: {"user_id": str(uuid4())},
                },
            }), encoding="utf-8")
            plan = build_legacy_migration_plan(database, mapping)
            self.assertEqual(len(plan.accounts), 2)
            self.assertEqual(plan.accounts[0].project_ids, ("p1", "p2"))
            self.assertEqual(plan.accounts[0].tenant_role_id, "member")
            self.assertIsNone(plan.accounts[1].tenant_role_id)

    def test_missing_claim_or_role_mapping_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "legacy.sqlite"
            mapping = Path(directory) / "mapping.json"
            self._database(database)
            mapping.write_text(json.dumps({
                "mapping_version": 1, "tenant_id": "tenant-a",
                "provider": "wechat-work", "role_mappings": {},
                "accounts": {},
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing account claim"):
                build_legacy_migration_plan(database, mapping)

    def test_missing_role_mapping_is_rejected_after_account_claim_is_present(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "legacy.sqlite"
            mapping = Path(directory) / "mapping.json"
            self._database(database)
            mapping.write_text(json.dumps({
                "mapping_version": 1, "tenant_id": "tenant-a",
                "provider": "wechat-work", "role_mappings": {},
                "accounts": {
                    hashlib.sha256(b"wx-user-1").hexdigest()[:16]:
                        {"user_id": str(uuid4())},
                    hashlib.sha256(b"wx-user-2").hexdigest()[:16]:
                        {"user_id": str(uuid4())},
                },
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "has no explicit role mapping"):
                build_legacy_migration_plan(database, mapping)

    def test_role_mapping_must_explicitly_define_both_role_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "legacy.sqlite"
            mapping = Path(directory) / "mapping.json"
            self._database(database)
            mapping.write_text(json.dumps({
                "mapping_version": 1, "tenant_id": "tenant-a",
                "provider": "wechat-work",
                "role_mappings": {
                    "member": {"tenant_role_id": "member"},
                    "viewer": {"tenant_role_id": None, "project_role_id": None},
                },
                "accounts": {
                    hashlib.sha256(b"wx-user-1").hexdigest()[:16]:
                        {"user_id": str(uuid4())},
                    hashlib.sha256(b"wx-user-2").hexdigest()[:16]:
                        {"user_id": str(uuid4())},
                },
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must explicitly define"):
                build_legacy_migration_plan(database, mapping)

    def test_mapping_template_never_overwrites_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "legacy.sqlite"
            output = Path(directory) / "mapping.json"
            self._database(database)
            output.write_text("keep", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                write_mapping_template(
                    database, output, tenant_id="tenant-a", provider="wechat-work",
                )
            self.assertEqual(output.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()


DSN = os.environ.get("AGENT_PLATFORM_TEST_DSN")
if DSN:
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row

    from agent_platform.identity.bootstrap import bootstrap_tenant_owner
    from agent_platform.identity.legacy_migration import (
        CONFIRMATION, apply_legacy_migration_plan,
    )


@unittest.skipUnless(DSN, "AGENT_PLATFORM_TEST_DSN is not configured")
class LegacyIdentityMigrationPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tenant_id = f"legacy-migration-{uuid4().hex}"
        migration_dir = Path("agent_platform/storage/postgres")
        async with await AsyncConnection.connect(DSN, autocommit=True) as connection:
            for migration_path in sorted(migration_dir.glob("*.sql")):
                await connection.execute(migration_path.read_text(), prepare=False)
        self.connection = await AsyncConnection.connect(DSN, row_factory=dict_row)
        await self.connection.execute(
            "INSERT INTO platform_tenants (tenant_id, display_name) VALUES (%s, %s)",
            (self.tenant_id, "Legacy Migration Integration"),
        )
        self.user_id = await bootstrap_tenant_owner(
            self.connection, tenant_id=self.tenant_id,
            email=f"owner-{uuid4().hex}@example.com",
            display_name="Migration Owner",
            password="correct horse battery staple",
        )
        self.project_id = f"project-{uuid4().hex}"
        await self.connection.execute(
            """INSERT INTO platform_projects
               (tenant_id,project_id,slug,name)
               VALUES (%s,%s,%s,%s)""",
            (self.tenant_id, self.project_id, self.project_id, "Legacy Project"),
        )
        await self.connection.commit()
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "legacy.sqlite"
        with sqlite3.connect(self.database) as source:
            source.execute(
                """CREATE TABLE users (
                   open_id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL,
                   projects TEXT NOT NULL DEFAULT '', console_token TEXT,
                   created_at TEXT NOT NULL)"""
            )
            source.execute(
                "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)",
                ("legacy-open-id", "Legacy Owner", "member", self.project_id,
                 "must-not-import", "2025-01-01"),
            )
        self.mapping = Path(self.tempdir.name) / "mapping.json"
        self.mapping.write_text(json.dumps({
            "mapping_version": 1,
            "tenant_id": self.tenant_id,
            "provider": "legacy-wechat",
            "role_mappings": {
                "member": {"tenant_role_id": "member", "project_role_id": None},
            },
            "accounts": {
                hashlib.sha256(b"legacy-open-id").hexdigest()[:16]:
                    {"user_id": str(self.user_id)},
            },
        }), encoding="utf-8")

    async def asyncTearDown(self):
        await self.connection.close()
        self.tempdir.cleanup()

    async def test_import_is_atomic_audited_and_idempotent(self):
        plan = build_legacy_migration_plan(self.database, self.mapping)
        first = await apply_legacy_migration_plan(
            self.connection, plan, confirmation=CONFIRMATION,
        )
        self.assertEqual(first["external_identities_created"], 1)
        self.assertEqual(first["project_memberships_created"], 1)
        self.assertEqual(first["role_bindings_created"], 1)

        second = await apply_legacy_migration_plan(
            self.connection, plan, confirmation=CONFIRMATION,
        )
        self.assertEqual(second["external_identities_created"], 0)
        self.assertEqual(second["project_memberships_created"], 0)
        self.assertEqual(second["role_bindings_created"], 0)

        cursor = await self.connection.execute(
            """SELECT count(*) AS n FROM platform_external_identities
               WHERE provider='legacy-wechat' AND provider_subject='legacy-open-id'
                 AND user_id=%s""",
            (self.user_id,),
        )
        self.assertEqual((await cursor.fetchone())["n"], 1)
        cursor = await self.connection.execute(
            """SELECT count(*) AS n FROM platform_identity_audit
               WHERE tenant_id=%s AND action='identity.legacy_imported'""",
            (self.tenant_id,),
        )
        self.assertEqual((await cursor.fetchone())["n"], 2)

    async def test_failed_project_validation_rolls_back_identity_and_roles(self):
        plan = build_legacy_migration_plan(self.database, self.mapping)
        from dataclasses import replace
        bad_account = replace(plan.accounts[0], project_ids=("missing-project",))
        bad_plan = replace(plan, accounts=(bad_account,))
        with self.assertRaisesRegex(ValueError, "not an active project"):
            await apply_legacy_migration_plan(
                self.connection, bad_plan, confirmation=CONFIRMATION,
            )
        cursor = await self.connection.execute(
            """SELECT 1 FROM platform_external_identities
               WHERE provider='legacy-wechat' AND provider_subject='legacy-open-id'"""
        )
        self.assertIsNone(await cursor.fetchone())
        cursor = await self.connection.execute(
            """SELECT 1 FROM platform_role_bindings
               WHERE tenant_id=%s AND subject_id=%s AND role_id='member'
                 AND resource_type='tenant' AND revoked_at IS NULL""",
            (self.tenant_id, str(self.user_id)),
        )
        self.assertIsNone(await cursor.fetchone())

