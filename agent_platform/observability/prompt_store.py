"""PostgreSQL-backed immutable prompt versions and deployment state."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from agent_platform.observability.prompts import PromptRegistry, PromptVariant, PromptVersion


class PostgresPromptRegistry:
    """Persistent prompt registry; version rows are immutable, deployment pointers are mutable."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connect = connection_factory

    async def register(self, prompt_id: str, template: str, *, created_by: str) -> PromptVersion:
        if not prompt_id.strip() or not created_by.strip() or not template.strip():
            raise ValueError("prompt_id, template and created_by are required")
        variables = PromptRegistry._variables(template)
        digest = hashlib.sha256(template.encode("utf-8")).hexdigest()
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))", (prompt_id,),
                )
                cursor = await connection.execute(
                    "SELECT COALESCE(MAX(version),0)+1 AS next_version "
                    "FROM platform_prompt_versions WHERE prompt_id=%s",
                    (prompt_id,),
                )
                row = await cursor.fetchone()
                version = int(row["next_version"])
                await connection.execute(
                    """INSERT INTO platform_prompt_versions
                       (prompt_id,version,template,variables,content_hash,created_by)
                       VALUES (%s,%s,%s,%s,%s,%s)""",
                    (prompt_id, version, template, list(variables), digest, created_by),
                )
                await self._audit(
                    connection, prompt_id, "version.registered", created_by,
                    {"version": version, "content_hash": digest, "variables": list(variables)},
                )
        return PromptVersion(prompt_id, version, template, variables, digest)

    async def resolve(self, prompt_id: str, *, version: int) -> PromptVersion:
        if not isinstance(prompt_id, str) or not prompt_id.strip():
            raise ValueError("prompt_id is required")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError("version must be a positive integer")
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT prompt_id,version,template,variables,content_hash
                   FROM platform_prompt_versions WHERE prompt_id=%s AND version=%s""",
                (prompt_id, version),
            )
            row = await cursor.fetchone()
        if row is None:
            raise KeyError("prompt version does not exist")
        return PromptVersion(
            row["prompt_id"], int(row["version"]), row["template"],
            tuple(row["variables"]), row["content_hash"],
        )

    async def list_versions(self, prompt_id: str) -> list[PromptVersion]:
        if not isinstance(prompt_id, str) or not prompt_id.strip():
            raise ValueError("prompt_id is required")
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT prompt_id,version,template,variables,content_hash
                   FROM platform_prompt_versions WHERE prompt_id=%s ORDER BY version""",
                (prompt_id,),
            )
            rows = await cursor.fetchall()
        return [
            PromptVersion(
                row["prompt_id"], int(row["version"]), row["template"],
                tuple(row["variables"]), row["content_hash"],
            )
            for row in rows
        ]

    async def create_variant(
        self, prompt_id: str, name: str, version: int, *, created_by: str,
    ) -> PromptVariant:
        name = PromptRegistry._validate_variant_name(name)
        if not isinstance(created_by, str) or not created_by.strip():
            raise ValueError("created_by is required")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ValueError("version must be a positive integer")
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    "SELECT 1 FROM platform_prompt_versions WHERE prompt_id=%s AND version=%s",
                    (prompt_id, version),
                )
                if await cursor.fetchone() is None:
                    raise KeyError("prompt version does not exist")
                await connection.execute(
                    """INSERT INTO platform_prompt_variants
                       (prompt_id, variant_name, current_version, status, created_by, updated_by)
                       VALUES (%s,%s,%s,'active',%s,%s)""",
                    (prompt_id, name, version, created_by, created_by),
                )
                await connection.execute(
                    """INSERT INTO platform_prompt_variant_versions
                       (prompt_id, variant_name, version, changed_by)
                       VALUES (%s,%s,%s,%s)""",
                    (prompt_id, name, version, created_by),
                )
                await self._audit(
                    connection, prompt_id, "variant.created", created_by,
                    {"variant": name, "version": version},
                )
        return PromptVariant(prompt_id, name, version, "active", created_by, created_by)

    async def assign_variant(
        self, prompt_id: str, name: str, version: int, *, updated_by: str,
    ) -> PromptVariant:
        name = PromptRegistry._validate_variant_name(name)
        if not isinstance(updated_by, str) or not updated_by.strip():
            raise ValueError("updated_by is required")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ValueError("version must be a positive integer")
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """SELECT status, created_by FROM platform_prompt_variants
                       WHERE prompt_id=%s AND variant_name=%s FOR UPDATE""",
                    (prompt_id, name),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise KeyError("prompt variant does not exist")
                if row["status"] != "active":
                    raise ValueError("deprecated prompt variant cannot be changed")
                version_cursor = await connection.execute(
                    "SELECT 1 FROM platform_prompt_versions WHERE prompt_id=%s AND version=%s",
                    (prompt_id, version),
                )
                if await version_cursor.fetchone() is None:
                    raise KeyError("prompt version does not exist")
                await connection.execute(
                    """UPDATE platform_prompt_variants
                       SET current_version=%s,updated_by=%s,updated_at=now()
                       WHERE prompt_id=%s AND variant_name=%s""",
                    (version, updated_by, prompt_id, name),
                )
                await connection.execute(
                    """INSERT INTO platform_prompt_variant_versions
                       (prompt_id, variant_name, version, changed_by)
                       VALUES (%s,%s,%s,%s)
                       ON CONFLICT (prompt_id, variant_name, version) DO NOTHING""",
                    (prompt_id, name, version, updated_by),
                )
                await self._audit(
                    connection, prompt_id, "variant.version_assigned", updated_by,
                    {"variant": name, "version": version},
                )
                return PromptVariant(
                    prompt_id, name, version, "active", row["created_by"], updated_by,
                )

    async def deprecate_variant(
        self, prompt_id: str, name: str, *, updated_by: str,
    ) -> PromptVariant:
        name = PromptRegistry._validate_variant_name(name)
        if not isinstance(updated_by, str) or not updated_by.strip():
            raise ValueError("updated_by is required")
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """SELECT current_version,status,created_by
                       FROM platform_prompt_variants
                       WHERE prompt_id=%s AND variant_name=%s FOR UPDATE""",
                    (prompt_id, name),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise KeyError("prompt variant does not exist")
                if row["status"] == "deprecated":
                    return PromptVariant(
                        prompt_id, name, int(row["current_version"]), "deprecated",
                        row["created_by"], updated_by,
                    )
                deployment_cursor = await connection.execute(
                    """SELECT 1 FROM platform_prompt_deployments
                       WHERE prompt_id=%s
                         AND (active_version=%s OR canary_version=%s)
                       LIMIT 1""",
                    (prompt_id, row["current_version"], row["current_version"]),
                )
                if await deployment_cursor.fetchone() is not None:
                    raise ValueError("deployed prompt variant cannot be deprecated")
                await connection.execute(
                    """UPDATE platform_prompt_variants
                       SET status='deprecated',updated_by=%s,updated_at=now()
                       WHERE prompt_id=%s AND variant_name=%s""",
                    (updated_by, prompt_id, name),
                )
                await self._audit(
                    connection, prompt_id, "variant.deprecated", updated_by,
                    {"variant": name, "version": int(row["current_version"])},
                )
        return PromptVariant(
            prompt_id, name, int(row["current_version"]), "deprecated",
            row["created_by"], updated_by,
        )

    async def list_variants(self, prompt_id: str) -> list[PromptVariant]:
        if not isinstance(prompt_id, str) or not prompt_id.strip():
            raise ValueError("prompt_id is required")
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT prompt_id,variant_name,current_version,status,created_by,updated_by
                   FROM platform_prompt_variants WHERE prompt_id=%s
                   ORDER BY variant_name""",
                (prompt_id,),
            )
            rows = await cursor.fetchall()
        return [PromptVariant(
            row["prompt_id"], row["variant_name"], int(row["current_version"]),
            row["status"], row["created_by"], row["updated_by"],
        ) for row in rows]

    async def variant_history(self, prompt_id: str, name: str) -> list[dict[str, Any]]:
        name = PromptRegistry._validate_variant_name(name)
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT version,changed_by,created_at
                   FROM platform_prompt_variant_versions
                   WHERE prompt_id=%s AND variant_name=%s
                   ORDER BY created_at,event_id""",
                (prompt_id, name),
            )
            rows = await cursor.fetchall()
        return [{
            "version": int(row["version"]), "changed_by": row["changed_by"],
            "created_at": row["created_at"],
        } for row in rows]

    @staticmethod
    async def _audit(connection: Any, prompt_id: str, action: str,
                     actor_id: str, details: dict[str, Any]) -> None:
        await connection.execute(
            """INSERT INTO platform_prompt_audit_events
               (prompt_id, action, actor_id, details)
               VALUES (%s,%s,%s,%s::jsonb)""",
            (prompt_id, action, actor_id, json.dumps(details, sort_keys=True)),
        )

    async def activate(self, prompt_id: str, version: int, *, updated_by: str,
                      evaluation_run_id: str | None = None) -> None:
        await self._set_active(prompt_id, version, updated_by=updated_by,
                               action="version.activated",
                               evaluation_run_id=evaluation_run_id)

    async def rollback(self, prompt_id: str, version: int, *, updated_by: str,
                       evaluation_run_id: str | None = None) -> None:
        await self._set_active(prompt_id, version, updated_by=updated_by,
                               action="version.rolled_back",
                               evaluation_run_id=evaluation_run_id)

    async def _set_active(self, prompt_id: str, version: int, *,
                          updated_by: str, action: str, evaluation_run_id: str | None) -> None:
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ValueError("version must be a positive integer")
        if not isinstance(updated_by, str) or not updated_by.strip():
            raise ValueError("updated_by is required")
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    "SELECT 1 FROM platform_prompt_versions WHERE prompt_id=%s AND version=%s",
                    (prompt_id, version),
                )
                if await cursor.fetchone() is None:
                    raise KeyError("prompt version does not exist")
                await connection.execute(
                    """INSERT INTO platform_prompt_deployments
                       (prompt_id,active_version,canary_version,canary_percentage,updated_by)
                       VALUES (%s,%s,NULL,0,%s)
                       ON CONFLICT (prompt_id) DO UPDATE SET
                         active_version=EXCLUDED.active_version,canary_version=NULL,
                         canary_percentage=0,updated_by=EXCLUDED.updated_by,updated_at=now()""",
                    (prompt_id, version, updated_by),
                )
                await self._audit(connection, prompt_id, action, updated_by,
                                  {"active_version": version,
                                   "evaluation_run_id": evaluation_run_id})

    async def set_canary(self, prompt_id: str, candidate_version: int,
                         percentage: float, *, updated_by: str,
                         evaluation_run_id: str | None = None) -> None:
        if not 0 < percentage <= 100:
            raise ValueError("canary percentage must be in (0, 100]")
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """SELECT d.active_version FROM platform_prompt_deployments d
                       JOIN platform_prompt_versions v
                         ON v.prompt_id=d.prompt_id AND v.version=%s
                       WHERE d.prompt_id=%s FOR UPDATE""",
                    (candidate_version, prompt_id),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise KeyError("prompt deployment or candidate version does not exist")
                if int(row["active_version"]) == candidate_version:
                    raise ValueError("canary candidate must differ from active version")
                await connection.execute(
                    """UPDATE platform_prompt_deployments
                       SET canary_version=%s,canary_percentage=%s,updated_by=%s,updated_at=now()
                       WHERE prompt_id=%s""",
                    (candidate_version, percentage, updated_by, prompt_id),
                )
                await self._audit(
                    connection, prompt_id, "canary.configured", updated_by,
                    {"canary_version": candidate_version, "percentage": float(percentage),
                     "evaluation_run_id": evaluation_run_id},
                )

    async def list_audit(self, prompt_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        if not isinstance(prompt_id, str) or not prompt_id.strip():
            raise ValueError("prompt_id is required")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT event_id, prompt_id, action, actor_id, details, created_at
                   FROM platform_prompt_audit_events
                   WHERE prompt_id=%s ORDER BY event_id DESC LIMIT %s""",
                (prompt_id, limit),
            )
            rows = await cursor.fetchall()
        return [{
            "event_id": str(row["event_id"]), "prompt_id": row["prompt_id"],
            "action": row["action"], "actor_id": row["actor_id"],
            "details": row["details"], "created_at": row["created_at"],
        } for row in rows]

    async def resolve(self, prompt_id: str, *, rollout_key: str = "",
                      version: int | None = None) -> PromptVersion:
        connection = await self._connect()
        async with connection:
            if version is None:
                cursor = await connection.execute(
                    """SELECT active_version,canary_version,canary_percentage
                       FROM platform_prompt_deployments WHERE prompt_id=%s""",
                    (prompt_id,),
                )
                deployment = await cursor.fetchone()
                if deployment is None:
                    raise KeyError("prompt has no active version")
                selected = int(deployment["active_version"])
                if deployment["canary_version"] is not None:
                    import hashlib as _hashlib
                    bucket = int(_hashlib.sha256(rollout_key.encode("utf-8")).hexdigest()[:8], 16) / 0x100000000 * 100
                    if bucket < float(deployment["canary_percentage"]):
                        selected = int(deployment["canary_version"])
            else:
                selected = version
            cursor = await connection.execute(
                """SELECT prompt_id,version,template,variables,content_hash
                   FROM platform_prompt_versions WHERE prompt_id=%s AND version=%s""",
                (prompt_id, selected),
            )
            row = await cursor.fetchone()
        if row is None:
            raise KeyError("prompt version does not exist")
        return PromptVersion(
            prompt_id=row["prompt_id"], version=int(row["version"]),
            template=row["template"], variables=tuple(row["variables"]),
            content_hash=row["content_hash"],
        )

    async def render(self, prompt_id: str, variables: dict[str, str], *,
                     rollout_key: str = "", version: int | None = None) -> tuple[str, PromptVersion]:
        prompt = await self.resolve(prompt_id, rollout_key=rollout_key, version=version)
        supplied, expected = set(variables), set(prompt.variables)
        if supplied != expected:
            raise ValueError(
                f"prompt variables mismatch; missing={sorted(expected-supplied)}, "
                f"extra={sorted(supplied-expected)}"
            )
        return prompt.template.format_map(variables), prompt
