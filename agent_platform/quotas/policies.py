"""Hierarchical tenant/project/subject quota policy resolution and audit."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol
from uuid import uuid4

RESOURCE_DIMENSIONS = frozenset({
    "tokens", "input_tokens", "output_tokens", "cost_usd", "concurrency",
    "storage_bytes", "requests", "tool_calls", "runtime_ms",
})
SCOPE_RANK = {"default": 0, "tenant": 1, "project": 2, "subject": 3}


@dataclass(frozen=True)
class QuotaPolicy:
    policy_id: str
    scope_type: str
    scope_id: str
    limits: Mapping[str, int | float]
    priority: int = 100
    enabled: bool = True
    version: int = 1
    tenant_id: str | None = None
    created_by: str = "system"

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, str) or not self.policy_id.strip():
            raise ValueError("policy_id is required")
        if self.scope_type not in SCOPE_RANK:
            raise ValueError("scope_type must be default, tenant, project or subject")
        if self.scope_type == "default":
            if self.scope_id != "*":
                raise ValueError("default scope_id must be '*'")
        elif not isinstance(self.scope_id, str) or not self.scope_id.strip():
            raise ValueError("scope_id is required")
        if self.scope_type != "default" and (not self.tenant_id or not self.tenant_id.strip()):
            raise ValueError("tenant_id is required for scoped policies")
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise ValueError("priority must be an integer")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("version must be a positive integer")
        if not isinstance(self.enabled, bool) or not self.limits:
            raise ValueError("enabled must be boolean and limits must not be empty")
        for resource, limit in self.limits.items():
            if resource not in RESOURCE_DIMENSIONS:
                raise ValueError(f"unsupported quota resource: {resource}")
            if isinstance(limit, bool) or not isinstance(limit, (int, float)):
                raise ValueError(f"{resource} limit must be numeric")
            if not math.isfinite(limit) or limit < 0:
                raise ValueError(f"{resource} limit must be finite and non-negative")


@dataclass(frozen=True)
class QuotaPolicyContext:
    tenant_id: str
    project_id: str | None = None
    subject_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, str) or not self.tenant_id.strip():
            raise ValueError("tenant_id is required")


@dataclass(frozen=True)
class ResolvedQuotaPolicy:
    tenant_id: str
    limits: Mapping[str, int | float]
    sources: Mapping[str, str]
    policy_versions: Mapping[str, int]
    resolved_at: datetime


@dataclass(frozen=True)
class QuotaPolicyAudit:
    audit_id: str
    tenant_id: str | None
    actor_id: str
    action: str
    policy_id: str
    policy_version: int
    before: Mapping[str, Any] | None
    after: Mapping[str, Any] | None
    occurred_at: datetime
    payload_sha256: str


class QuotaPolicyRepository(Protocol):
    async def put_policy(self, policy: QuotaPolicy) -> None: ...
    async def list_policies(self, *, tenant_id: str) -> list[QuotaPolicy]: ...
    async def append_audit(self, event: QuotaPolicyAudit) -> None: ...
    async def put_policy_and_audit(
        self, policy: QuotaPolicy, event: QuotaPolicyAudit,
    ) -> None: ...


class InMemoryQuotaPolicyRepository:
    """Deterministic adapter for tests; production should use PostgresQuotaPolicyRepository."""
    def __init__(self, defaults: Mapping[str, int | float] | None = None) -> None:
        self.defaults = dict(defaults or {})
        self.policies: dict[tuple[str | None, str, str, str], QuotaPolicy] = {}
        self.audit_events: list[QuotaPolicyAudit] = []

    async def put_policy(self, policy: QuotaPolicy) -> None:
        key = (policy.tenant_id, policy.scope_type, policy.scope_id, policy.policy_id)
        self.policies[key] = policy

    async def list_policies(self, *, tenant_id: str) -> list[QuotaPolicy]:
        return [
            item for item in self.policies.values()
            if item.scope_type == "default" or item.tenant_id == tenant_id
        ]


    async def append_audit(self, event: QuotaPolicyAudit) -> None:
        self.audit_events.append(event)

    async def put_policy_and_audit(self, policy: QuotaPolicy, event: QuotaPolicyAudit) -> None:
        await self.put_policy(policy)
        await self.append_audit(event)


class QuotaPolicyService:
    def __init__(self, repository: QuotaPolicyRepository) -> None:
        self.repository = repository

    async def configure(
        self, policy: QuotaPolicy, *, actor_id: str,
        authorize: Any,
    ) -> QuotaPolicyAudit:
        if not isinstance(actor_id, str) or not actor_id.strip():
            raise ValueError("actor_id is required")
        allowed = authorize(policy)
        if hasattr(allowed, "__await__"):
            allowed = await allowed
        if allowed is not True:
            raise PermissionError("quota policy scope is not authorized")
        existing = await self.repository.list_policies(
            tenant_id=policy.tenant_id or "__default__",
        )
        previous = next((item for item in existing if (
            item.policy_id == policy.policy_id and item.scope_type == policy.scope_type
            and item.scope_id == policy.scope_id
        )), None)
        if previous is not None and policy.version <= previous.version:
            raise ValueError("policy version must increase monotonically")
        before = self._policy_payload(previous) if previous else None
        after = self._policy_payload(policy)
        audit_id = str(uuid4())
        payload = {
            "audit_id": audit_id, "tenant_id": policy.tenant_id,
            "actor_id": actor_id, "action": "quota.policy.updated" if previous else "quota.policy.created",
            "policy_id": policy.policy_id, "policy_version": policy.version,
            "before": before, "after": after,
        }
        digest = hashlib.sha256(json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode()).hexdigest()
        event = QuotaPolicyAudit(
            audit_id=audit_id, tenant_id=policy.tenant_id, actor_id=actor_id,
            action=str(payload["action"]), policy_id=policy.policy_id,
            policy_version=policy.version, before=before, after=after,
            occurred_at=datetime.now(timezone.utc), payload_sha256=digest,
        )
        atomic_writer = getattr(self.repository, "put_policy_and_audit", None)
        if not callable(atomic_writer):
            raise RuntimeError("quota policy repository must support atomic policy+audit writes")
        await atomic_writer(policy, event)
        return event

    async def resolve(self, context: QuotaPolicyContext) -> ResolvedQuotaPolicy:
        policies = await self.repository.list_policies(tenant_id=context.tenant_id)
        eligible: list[QuotaPolicy] = []
        for policy in policies:
            if not policy.enabled:
                continue
            if policy.scope_type == "default":
                eligible.append(policy)
            elif policy.scope_type == "tenant" and policy.tenant_id == context.tenant_id and policy.scope_id == context.tenant_id:
                eligible.append(policy)
            elif policy.scope_type == "project" and policy.tenant_id == context.tenant_id and context.project_id == policy.scope_id:
                eligible.append(policy)
            elif policy.scope_type == "subject" and policy.tenant_id == context.tenant_id and context.subject_id == policy.scope_id:
                eligible.append(policy)
        # More specific scope wins. Priority (higher first), then version and ID
        # provide deterministic tie-breaking within a scope.
        eligible.sort(key=lambda p: (
            SCOPE_RANK[p.scope_type], p.priority, p.version, p.policy_id,
        ), reverse=True)
        limits: dict[str, int | float] = {}
        sources: dict[str, str] = {}
        versions: dict[str, int] = {}
        for policy in eligible:
            for resource, limit in policy.limits.items():
                if resource not in limits:
                    limits[resource] = limit
                    sources[resource] = policy.policy_id
                    versions[resource] = policy.version
        return ResolvedQuotaPolicy(
            tenant_id=context.tenant_id, limits=limits, sources=sources,
            policy_versions=versions, resolved_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _policy_payload(policy: QuotaPolicy | None) -> dict[str, Any] | None:
        if policy is None:
            return None
        return {
            "scope_type": policy.scope_type, "scope_id": policy.scope_id,
            "limits": dict(policy.limits), "priority": policy.priority,
            "enabled": policy.enabled, "version": policy.version,
        }


class PostgresQuotaPolicyRepository:
    """Shared PostgreSQL policy store with append-only configuration audit."""
    def __init__(self, connection_factory: Any) -> None:
        self._connect = connection_factory

    @classmethod
    def from_dsn(cls, dsn: str) -> "PostgresQuotaPolicyRepository":
        if not dsn:
            raise ValueError("PostgreSQL DSN is required")
        async def connect() -> Any:
            try:
                from psycopg import AsyncConnection
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError("Install psycopg[binary] for quota policy storage") from exc
            return await AsyncConnection.connect(dsn, row_factory=dict_row)
        return cls(connect)

    async def put_policy(self, policy: QuotaPolicy) -> None:
        import json
        connection = await self._connect()
        async with connection:
            await connection.execute(
                """INSERT INTO platform_quota_policies
                   (policy_id,tenant_id,scope_type,scope_id,limits,priority,enabled,version,created_by)
                   VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s)
                   ON CONFLICT (scope_type,tenant_id,scope_id,policy_id) DO UPDATE SET
                     limits=EXCLUDED.limits,priority=EXCLUDED.priority,enabled=EXCLUDED.enabled,
                     version=EXCLUDED.version,created_by=EXCLUDED.created_by,updated_at=now()
                   WHERE EXCLUDED.version > platform_quota_policies.version""",
                (policy.policy_id,policy.tenant_id or "",policy.scope_type,policy.scope_id,
                 json.dumps(dict(policy.limits),allow_nan=False),policy.priority,
                 policy.enabled,policy.version,policy.created_by),
            )

    async def list_policies(self, *, tenant_id: str) -> list[QuotaPolicy]:
        import json
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT policy_id,tenant_id,scope_type,scope_id,limits,priority,enabled,version,created_by
                   FROM platform_quota_policies WHERE tenant_id=%s OR scope_type='default'""",
                (tenant_id,),
            )
            rows = await cursor.fetchall()
        return [QuotaPolicy(
            policy_id=row["policy_id"],tenant_id=row["tenant_id"] or None,scope_type=row["scope_type"],
            scope_id=row["scope_id"],limits=json.loads(row["limits"]) if isinstance(row["limits"],str) else row["limits"],
            priority=row["priority"],enabled=row["enabled"],version=row["version"],created_by=row["created_by"],
        ) for row in rows]

    async def append_audit(self, event: QuotaPolicyAudit) -> None:
        import json
        connection = await self._connect()
        async with connection:
            await connection.execute(
                """INSERT INTO platform_quota_policy_audit
                   (audit_id,tenant_id,actor_id,action,policy_id,policy_version,
                    before_state,after_state,occurred_at,payload_sha256)
                   VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s)""",
                (event.audit_id,event.tenant_id,event.actor_id,event.action,event.policy_id,
                 event.policy_version,json.dumps(event.before) if event.before is not None else None,
                 json.dumps(event.after),event.occurred_at,event.payload_sha256),
            )

    async def put_policy_and_audit(
        self, policy: QuotaPolicy, event: QuotaPolicyAudit,
    ) -> None:
        """Persist a policy revision and its audit event in one transaction.

        The conditional upsert is the concurrency gate: if another writer has
        already stored this or a newer version, rowcount is zero and the audit
        insert is not attempted. An audit failure rolls back the policy change.
        """
        import json

        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """INSERT INTO platform_quota_policies
                       (policy_id,tenant_id,scope_type,scope_id,limits,priority,enabled,version,created_by)
                       VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s)
                       ON CONFLICT (scope_type,tenant_id,scope_id,policy_id) DO UPDATE SET
                         limits=EXCLUDED.limits,priority=EXCLUDED.priority,enabled=EXCLUDED.enabled,
                         version=EXCLUDED.version,created_by=EXCLUDED.created_by,updated_at=now()
                       WHERE EXCLUDED.version > platform_quota_policies.version""",
                    (policy.policy_id,policy.tenant_id or "",policy.scope_type,policy.scope_id,
                     json.dumps(dict(policy.limits),allow_nan=False),policy.priority,
                     policy.enabled,policy.version,policy.created_by),
                )
                if getattr(cursor, "rowcount", 1) != 1:
                    raise ValueError("policy version is stale or already exists")
                await connection.execute(
                    """INSERT INTO platform_quota_policy_audit
                       (audit_id,tenant_id,actor_id,action,policy_id,policy_version,
                        before_state,after_state,occurred_at,payload_sha256)
                       VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s)""",
                    (event.audit_id,event.tenant_id,event.actor_id,event.action,event.policy_id,
                     event.policy_version,json.dumps(event.before) if event.before is not None else None,
                     json.dumps(event.after),event.occurred_at,event.payload_sha256),
                )
