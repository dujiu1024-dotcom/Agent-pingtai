"""Explicit, auditable import of legacy SQLite users into platform identities.

The source database is always opened read-only. Legacy tokens are never read into
the migration plan. Every account and role mapping must be supplied explicitly.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

CONFIRMATION = "IMPORT-LEGACY-IDENTITIES"
REQUIRED_COLUMNS = {"open_id", "name", "role", "projects", "console_token", "created_at"}
_PROVIDER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


@dataclass(frozen=True)
class LegacyImportAccount:
    legacy_subject_ref: str
    provider_subject: str
    user_id: str
    display_name: str
    legacy_role: str
    project_ids: tuple[str, ...]
    tenant_role_id: str | None
    project_role_id: str | None


@dataclass(frozen=True)
class LegacyImportPlan:
    tenant_id: str
    provider: str
    accounts: tuple[LegacyImportAccount, ...]


def _read_legacy_rows(database_path: str | Path) -> list[dict[str, Any]]:
    path = Path(database_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        tables = {
            row["name"] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "users" not in tables:
            raise ValueError("legacy database does not contain a users table")
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(users)")
        }
        missing = sorted(REQUIRED_COLUMNS - columns)
        if missing:
            raise ValueError("legacy users table is missing required columns: " + ", ".join(missing))
        rows = connection.execute(
            """SELECT open_id, name, role, projects, created_at
               FROM users ORDER BY created_at, open_id"""
        ).fetchall()
    return [dict(row) for row in rows]


def write_mapping_template(
    database_path: str | Path, output_path: str | Path, *,
    tenant_id: str, provider: str,
) -> Path:
    """Write a manual account/role mapping template without exporting raw legacy IDs."""
    if not tenant_id.strip() or not _PROVIDER_RE.fullmatch(provider):
        raise ValueError("tenant_id and a valid provider slug are required")
    output = Path(output_path).expanduser().resolve()
    if output.exists():
        raise FileExistsError(output)
    rows = _read_legacy_rows(database_path)
    accounts: dict[str, dict[str, str]] = {}
    roles: dict[str, dict[str, str | None]] = {}
    for row in rows:
        open_id = str(row["open_id"] or "").strip()
        role = str(row["role"] or "").strip()
        if not open_id or not role:
            raise ValueError("all legacy users need a non-empty open_id and role before mapping")
        ref = hashlib.sha256(open_id.encode("utf-8")).hexdigest()[:16]
        if ref in accounts:
            raise ValueError("duplicate legacy subject reference")
        accounts[ref] = {"user_id": ""}
        roles.setdefault(role, {"tenant_role_id": "", "project_role_id": None})
    payload = {
        "mapping_version": 1,
        "tenant_id": tenant_id,
        "provider": provider,
        "role_mappings": roles,
        "accounts": accounts,
        "instructions": [
            "Map each user_id to an existing active platform user UUID.",
            "Map each legacy role to an existing tenant role ID; use null to grant no role.",
            "project_role_id is optional; project membership alone grants no permissions.",
            "Do not add raw legacy tokens or passwords to this file.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def build_legacy_migration_plan(
    database_path: str | Path, mapping_path: str | Path,
) -> LegacyImportPlan:
    """Validate a human-reviewed mapping file and build an import plan."""
    mapping = json.loads(Path(mapping_path).expanduser().read_text(encoding="utf-8"))
    if not isinstance(mapping, dict) or mapping.get("mapping_version") != 1:
        raise ValueError("unsupported legacy mapping version")
    tenant_id = str(mapping.get("tenant_id") or "").strip()
    provider = str(mapping.get("provider") or "").strip()
    if not tenant_id or not _PROVIDER_RE.fullmatch(provider):
        raise ValueError("mapping requires tenant_id and a valid provider slug")
    role_mappings = mapping.get("role_mappings")
    account_mappings = mapping.get("accounts")
    if not isinstance(role_mappings, dict) or not isinstance(account_mappings, dict):
        raise ValueError("role_mappings and accounts must be JSON objects")

    rows = _read_legacy_rows(database_path)
    expected_refs: set[str] = set()
    plan: list[LegacyImportAccount] = []
    seen_subjects: set[str] = set()
    for row in rows:
        subject = str(row["open_id"] or "").strip()
        role = str(row["role"] or "").strip()
        if not subject or not role:
            raise ValueError("legacy users with missing open_id or role require manual resolution")
        ref = hashlib.sha256(subject.encode("utf-8")).hexdigest()[:16]
        if ref in expected_refs or subject in seen_subjects:
            raise ValueError("duplicate legacy identity in source database")
        expected_refs.add(ref)
        seen_subjects.add(subject)

        account = account_mappings.get(ref)
        if not isinstance(account, dict):
            raise ValueError(f"missing account claim for legacy subject {ref}")
        try:
            user_id = str(UUID(str(account.get("user_id") or "")))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError(f"account claim {ref} must contain a valid platform user UUID") from exc

        role_mapping = role_mappings.get(role)
        if not isinstance(role_mapping, dict):
            raise ValueError(f"legacy role {role!r} has no explicit role mapping")
        if "tenant_role_id" not in role_mapping or "project_role_id" not in role_mapping:
            raise ValueError(f"legacy role {role!r} mapping must explicitly define tenant_role_id and project_role_id")
        tenant_role_id = role_mapping.get("tenant_role_id")
        project_role_id = role_mapping.get("project_role_id")
        tenant_role_id = str(tenant_role_id).strip() if tenant_role_id else None
        project_role_id = str(project_role_id).strip() if project_role_id else None
        project_ids = tuple(dict.fromkeys(
            item.strip() for item in str(row["projects"] or "").split(",") if item.strip()
        ))
        plan.append(LegacyImportAccount(
            legacy_subject_ref=ref,
            provider_subject=subject,
            user_id=user_id,
            display_name=str(row["name"] or "").strip(),
            legacy_role=role,
            project_ids=project_ids,
            tenant_role_id=tenant_role_id,
            project_role_id=project_role_id,
        ))

    extras = sorted(set(account_mappings) - expected_refs)
    if extras:
        raise ValueError("mapping contains unknown legacy subject references: " + ", ".join(extras))
    return LegacyImportPlan(tenant_id=tenant_id, provider=provider, accounts=tuple(plan))


async def apply_legacy_migration_plan(
    connection: Any, plan: LegacyImportPlan, *, confirmation: str,
) -> dict[str, int]:
    """Import claims and least-privilege memberships atomically into PostgreSQL.

    Existing users and active tenant memberships are required. This operation
    never creates passwords, grants an implicit administrator role, or imports
    legacy console tokens. Re-running the same plan is safe.
    """
    if confirmation != CONFIRMATION:
        raise ValueError("explicit confirmation string is required")
    imported_identities = 0
    created_project_memberships = 0
    created_role_bindings = 0

    async with connection.transaction():
        for account in plan.accounts:
            cursor = await connection.execute(
                """SELECT u.status, m.status AS membership_status
                   FROM platform_users u
                   JOIN platform_tenant_memberships m ON m.user_id=u.user_id
                   WHERE u.user_id=%s AND m.tenant_id=%s""",
                (account.user_id, plan.tenant_id),
            )
            user = await cursor.fetchone()
            if user is None or user["status"] != "active" or user["membership_status"] != "active":
                raise ValueError(f"claimed user {account.user_id} must be an active member of the target tenant")

            for role_id in (account.tenant_role_id, account.project_role_id):
                if role_id:
                    cursor = await connection.execute(
                        "SELECT 1 FROM platform_roles WHERE tenant_id=%s AND role_id=%s",
                        (plan.tenant_id, role_id),
                    )
                    if await cursor.fetchone() is None:
                        raise ValueError(f"mapped role {role_id!r} does not exist in the target tenant")

            cursor = await connection.execute(
                """INSERT INTO platform_external_identities
                   (external_identity_id, user_id, provider, provider_subject)
                   VALUES (%s,%s,%s,%s)
                   ON CONFLICT (provider, provider_subject) DO NOTHING
                   RETURNING user_id""",
                (str(uuid4()), account.user_id, plan.provider, account.provider_subject),
            )
            inserted = await cursor.fetchone()
            if inserted is not None:
                imported_identities += 1
            else:
                cursor = await connection.execute(
                    """SELECT user_id FROM platform_external_identities
                       WHERE provider=%s AND provider_subject=%s""",
                    (plan.provider, account.provider_subject),
                )
                existing = await cursor.fetchone()
                if existing is None or str(existing["user_id"]) != account.user_id:
                    raise ValueError("legacy identity is already claimed by a different platform user")

            if account.tenant_role_id:
                cursor = await connection.execute(
                    """INSERT INTO platform_role_bindings
                       (binding_id,tenant_id,subject_type,subject_id,resource_type,
                        resource_id,role_id,created_by)
                       SELECT %s,%s,'user',%s,'tenant',%s,%s,'legacy-migration'
                       WHERE NOT EXISTS (
                         SELECT 1 FROM platform_role_bindings
                         WHERE tenant_id=%s AND subject_type='user' AND subject_id=%s
                           AND resource_type='tenant' AND resource_id=%s
                           AND role_id=%s AND revoked_at IS NULL
                       )
                       RETURNING binding_id""",
                    (str(uuid4()), plan.tenant_id, account.user_id, plan.tenant_id,
                     account.tenant_role_id, plan.tenant_id, account.user_id,
                     plan.tenant_id, account.tenant_role_id),
                )
                if await cursor.fetchone() is not None:
                    created_role_bindings += 1

            for project_id in account.project_ids:
                cursor = await connection.execute(
                    """SELECT status FROM platform_projects
                       WHERE tenant_id=%s AND project_id=%s""",
                    (plan.tenant_id, project_id),
                )
                project = await cursor.fetchone()
                if project is None or project["status"] != "active":
                    raise ValueError(f"legacy project {project_id!r} is not an active project in the target tenant")
                cursor = await connection.execute(
                    """INSERT INTO platform_project_members
                       (tenant_id,project_id,user_id,status)
                       VALUES (%s,%s,%s,'active')
                       ON CONFLICT (tenant_id,project_id,user_id) DO NOTHING
                       RETURNING user_id""",
                    (plan.tenant_id, project_id, account.user_id),
                )
                membership_created = await cursor.fetchone() is not None
                if membership_created:
                    created_project_memberships += 1
                else:
                    cursor = await connection.execute(
                        """SELECT status FROM platform_project_members
                           WHERE tenant_id=%s AND project_id=%s AND user_id=%s""",
                        (plan.tenant_id, project_id, account.user_id),
                    )
                    existing_membership = await cursor.fetchone()
                    if existing_membership is None or existing_membership["status"] != "active":
                        raise ValueError(f"existing project membership for {project_id!r} is not active")

                if account.project_role_id:
                    cursor = await connection.execute(
                        """INSERT INTO platform_role_bindings
                           (binding_id,tenant_id,subject_type,subject_id,resource_type,
                            resource_id,role_id,created_by)
                           SELECT %s,%s,'user',%s,'project',%s,%s,'legacy-migration'
                           WHERE NOT EXISTS (
                             SELECT 1 FROM platform_role_bindings
                             WHERE tenant_id=%s AND subject_type='user' AND subject_id=%s
                               AND resource_type='project' AND resource_id=%s
                               AND role_id=%s AND revoked_at IS NULL
                           )
                           RETURNING binding_id""",
                        (str(uuid4()), plan.tenant_id, account.user_id, project_id,
                         account.project_role_id, plan.tenant_id, account.user_id,
                         project_id, account.project_role_id),
                    )
                    if await cursor.fetchone() is not None:
                        created_role_bindings += 1

            await connection.execute(
                """INSERT INTO platform_identity_audit
                   (tenant_id,actor_id,action,subject_id,outcome,detail)
                   VALUES (%s,'legacy-migration','identity.legacy_imported',%s,'succeeded',%s::jsonb)""",
                (plan.tenant_id, account.user_id, json.dumps({
                    "provider": plan.provider,
                    "legacy_subject_ref": account.legacy_subject_ref,
                    "legacy_role": account.legacy_role,
                    "project_count": len(account.project_ids),
                    "legacy_tokens_imported": False,
                })),
            )

    return {
        "accounts_processed": len(plan.accounts),
        "external_identities_created": imported_identities,
        "project_memberships_created": created_project_memberships,
        "role_bindings_created": created_role_bindings,
    }


async def _run_cli(args: argparse.Namespace) -> None:
    plan = build_legacy_migration_plan(args.db, args.mapping)
    summary = {
        "tenant_id": plan.tenant_id,
        "provider": plan.provider,
        "accounts": len(plan.accounts),
        "project_memberships": sum(len(x.project_ids) for x in plan.accounts),
        "legacy_tokens_imported": False,
        "dry_run": not args.apply,
    }
    if not args.apply:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return
    if not args.dsn or args.confirm != CONFIRMATION:
        raise ValueError("--apply requires --dsn and the exact --confirm string")
    from psycopg import AsyncConnection
    connection = await AsyncConnection.connect(args.dsn)
    try:
        result = await apply_legacy_migration_plan(
            connection, plan, confirmation=args.confirm,
        )
    finally:
        await connection.close()
    print(json.dumps({**summary, "dry_run": False, "result": result}, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate or import explicitly mapped legacy identities.")
    parser.add_argument("--db", required=True, help="Legacy SQLite database path (read-only)")
    parser.add_argument("--mapping", help="Human-reviewed mapping JSON")
    parser.add_argument("--template-output", help="Write a new mapping template and exit")
    parser.add_argument("--tenant-id", help="Target tenant ID for template generation")
    parser.add_argument("--provider", help="Verified external identity provider slug")
    parser.add_argument("--apply", action="store_true", help="Apply the validated plan to PostgreSQL")
    parser.add_argument("--dsn", help="PostgreSQL DSN; required with --apply")
    parser.add_argument("--confirm", help=f"Type exactly: {CONFIRMATION}")
    args = parser.parse_args()
    if args.template_output:
        if not args.tenant_id or not args.provider or args.mapping or args.apply:
            parser.error("--template-output requires --tenant-id and --provider, and cannot be combined with --mapping or --apply")
        output = write_mapping_template(
            args.db, args.template_output, tenant_id=args.tenant_id, provider=args.provider,
        )
        print(json.dumps({"template": str(output), "source_mutated": False}, ensure_ascii=False))
        return
    if not args.mapping:
        parser.error("--mapping is required unless --template-output is used")
    asyncio.run(_run_cli(args))


if __name__ == "__main__":
    main()
