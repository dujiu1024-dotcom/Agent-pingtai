"""PostgreSQL-backed immutable prompt versions and deployment state."""
from __future__ import annotations

import hashlib
from typing import Any, Callable

from agent_platform.observability.prompts import PromptRegistry, PromptVersion


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
        return PromptVersion(prompt_id, version, template, variables, digest)

    async def activate(self, prompt_id: str, version: int, *, updated_by: str) -> None:
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

    async def set_canary(self, prompt_id: str, candidate_version: int,
                         percentage: float, *, updated_by: str) -> None:
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
