"""Review pinned Skill dependencies before they can enter a Run snapshot."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol

from agent_platform.control_plane.registry import RegistryVersion


class SkillDependencyReviewError(ValueError):
    pass


@dataclass(frozen=True)
class SkillDependencyReview:
    tenant_id: str
    skill_id: str
    skill_version: int
    manifest_digest: str
    status: str
    dependencies: Mapping[str, str]
    findings: tuple[str, ...]
    reviewed_by: str
    reviewed_at: datetime


class DependencyAdvisoryScanner(Protocol):
    async def scan(self, package: str, pin: str) -> tuple[str, ...]: ...


class SkillDependencyReviewStore(Protocol):
    async def save(self, review: SkillDependencyReview) -> None: ...
    async def get(
        self, tenant_id: str, skill_id: str, skill_version: int,
    ) -> SkillDependencyReview | None: ...


def skill_manifest_digest(definition: Mapping[str, Any]) -> str:
    manifest = definition.get("manifest")
    if not isinstance(manifest, Mapping):
        raise SkillDependencyReviewError("Skill registry definition has no manifest")
    canonical = json.dumps(dict(manifest), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class InMemorySkillDependencyReviewStore:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str, int], SkillDependencyReview] = {}

    async def save(self, review: SkillDependencyReview) -> None:
        self._records[(review.tenant_id, review.skill_id, review.skill_version)] = review

    async def get(self, tenant_id: str, skill_id: str, skill_version: int) -> SkillDependencyReview | None:
        return self._records.get((tenant_id, skill_id, skill_version))


class PostgresSkillDependencyReviewStore:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connect = connection_factory

    async def save(self, review: SkillDependencyReview) -> None:
        try:
            from psycopg.types.json import Jsonb
        except ImportError as exc:
            raise RuntimeError("Install psycopg[binary,pool] for PostgreSQL storage") from exc
        connection = await self._connect()
        async with connection:
            await connection.execute(
                """INSERT INTO platform_skill_dependency_reviews
                   (tenant_id,skill_id,skill_version,manifest_digest,status,
                    dependencies,findings,reviewed_by,reviewed_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (tenant_id,skill_id,skill_version) DO UPDATE SET
                     manifest_digest=EXCLUDED.manifest_digest,
                     status=EXCLUDED.status, dependencies=EXCLUDED.dependencies,
                     findings=EXCLUDED.findings, reviewed_by=EXCLUDED.reviewed_by,
                     reviewed_at=EXCLUDED.reviewed_at""",
                (review.tenant_id, review.skill_id, review.skill_version,
                 review.manifest_digest, review.status, Jsonb(dict(review.dependencies)),
                 Jsonb(list(review.findings)), review.reviewed_by, review.reviewed_at),
            )

    async def get(self, tenant_id: str, skill_id: str, skill_version: int) -> SkillDependencyReview | None:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT tenant_id,skill_id,skill_version,manifest_digest,status,
                          dependencies,findings,reviewed_by,reviewed_at
                   FROM platform_skill_dependency_reviews
                   WHERE tenant_id=%s AND skill_id=%s AND skill_version=%s""",
                (tenant_id, skill_id, skill_version),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return SkillDependencyReview(
            tenant_id=row["tenant_id"], skill_id=row["skill_id"],
            skill_version=int(row["skill_version"]), manifest_digest=row["manifest_digest"],
            status=row["status"], dependencies=dict(row["dependencies"] or {}),
            findings=tuple(row["findings"] or ()), reviewed_by=row["reviewed_by"],
            reviewed_at=row["reviewed_at"],
        )


class SkillDependencyReviewer:
    """Require exact allowlist matches and a clean optional vulnerability scan."""

    def __init__(
        self, *, store: SkillDependencyReviewStore,
        approved_pins: Mapping[str, tuple[str, ...]],
        scanner: DependencyAdvisoryScanner | None = None,
        approved_tools: frozenset[str] | None = None,
    ) -> None:
        self.store = store
        self.approved_pins = {
            package.casefold(): frozenset(pins)
            for package, pins in approved_pins.items()
        }
        self.scanner = scanner
        self.approved_tools = (
            frozenset(tool.casefold() for tool in approved_tools)
            if approved_tools is not None else None
        )

    async def review(
        self, *, item: RegistryVersion, actor_id: str,
    ) -> SkillDependencyReview:
        if item.kind != "skill" or item.status != "published":
            raise SkillDependencyReviewError("only published Skill versions can be reviewed")
        if not actor_id.strip():
            raise ValueError("actor_id is required")
        manifest = item.definition.get("manifest")
        if not isinstance(manifest, Mapping):
            raise SkillDependencyReviewError("Skill manifest is invalid")
        raw_dependencies = manifest.get("dependencies", {})
        if not isinstance(raw_dependencies, Mapping):
            raise SkillDependencyReviewError("Skill dependencies must be an object")
        dependencies: dict[str, str] = {}
        findings: list[str] = []
        raw_tools = manifest.get("tool_allowlist", manifest.get("tools", ()))
        if not isinstance(raw_tools, (list, tuple)):
            findings.append("invalid_tool_allowlist")
        else:
            requested_tools = tuple(
                tool.strip() for tool in raw_tools
                if isinstance(tool, str) and tool.strip()
            )
            if (
                len(requested_tools) != len(raw_tools)
                or len(set(requested_tools)) != len(requested_tools)
            ):
                findings.append("invalid_tool_allowlist")
            for tool in requested_tools:
                if self.approved_tools is None:
                    findings.append("tool_allowlist_not_configured:" + tool[:100])
                elif tool.casefold() not in self.approved_tools:
                    findings.append("tool_not_allowlisted:" + tool[:100])
        for raw_package, raw_pin in raw_dependencies.items():
            if not isinstance(raw_package, str) or not isinstance(raw_pin, str):
                findings.append("invalid_dependency_entry")
                continue
            package = raw_package.strip()
            pin = raw_pin.strip()
            if not package or not pin or pin not in self.approved_pins.get(package.casefold(), frozenset()):
                findings.append("dependency_not_allowlisted:" + package[:100])
                continue
            dependencies[package] = pin
            if self.scanner is None:
                findings.append("dependency_scanner_not_configured:" + package[:100])
            else:
                try:
                    advisories = await self.scanner.scan(package, pin)
                except Exception:
                    findings.append("dependency_scan_unavailable:" + package[:100])
                else:
                    findings.extend(
                        "dependency_advisory:" + package[:100] + ":" + str(code)[:80]
                        for code in advisories
                    )
        digest = skill_manifest_digest(item.definition)
        result = SkillDependencyReview(
            tenant_id=item.tenant_id, skill_id=item.item_id, skill_version=item.version,
            manifest_digest=digest, status="approved" if not findings else "rejected",
            dependencies=dependencies, findings=tuple(sorted(set(findings))),
            reviewed_by=actor_id, reviewed_at=datetime.now(timezone.utc),
        )
        await self.store.save(result)
        return result

    async def require_approved(
        self, *, tenant_id: str, item: RegistryVersion,
    ) -> SkillDependencyReview:
        review = await self.store.get(tenant_id, item.item_id, item.version)
        if (
            tenant_id != item.tenant_id or review is None
            or review.status != "approved"
            or review.manifest_digest != skill_manifest_digest(item.definition)
        ):
            raise SkillDependencyReviewError("Skill dependency review is missing, rejected, or stale")
        return review
