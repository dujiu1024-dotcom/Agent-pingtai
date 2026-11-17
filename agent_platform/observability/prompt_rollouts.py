"""Deterministic, auditable traffic routing for tenant-scoped prompt variants."""
from __future__ import annotations

import hashlib
import inspect
import json
import math
import time
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from agent_platform.observability.prompts import tenant_prompt_key


@dataclass(frozen=True)
class PromptRolloutPolicy:
    tenant_id: str
    prompt_id: str
    weights: tuple[tuple[str, float], ...]
    tag_routes: tuple[tuple[str, str], ...] = ()
    tenant_routes: tuple[tuple[str, str], ...] = ()
    updated_by: str = ""
    revision: int = 1

    def __post_init__(self) -> None:
        if not self.tenant_id.strip() or not self.prompt_id.strip():
            raise ValueError("tenant_id and prompt_id are required")
        if not self.weights:
            raise ValueError("at least one weighted variant is required")
        names = [name for name, _ in self.weights]
        if len(names) != len(set(names)):
            raise ValueError("weighted variants must be unique")
        if any(not name.strip() for name in names):
            raise ValueError("variant names are required")
        values = [weight for _, weight in self.weights]
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("weights must be finite non-negative numbers")
        if not math.isclose(sum(values), 100.0, abs_tol=1e-6):
            raise ValueError("variant weights must sum to 100")
        for pairs, label in ((self.tag_routes, "tag"), (self.tenant_routes, "tenant")):
            keys = [key for key, _ in pairs]
            if len(keys) != len(set(keys)) or any(not key.strip() or not variant.strip()
                                                   for key, variant in pairs):
                raise ValueError(f"{label} routes require unique non-empty keys and variants")
        if not isinstance(self.revision, int) or isinstance(self.revision, bool) or self.revision < 1:
            raise ValueError("revision must be a positive integer")


@dataclass(frozen=True)
class PromptRouteDecision:
    tenant_id: str
    prompt_id: str
    variant: str
    rule: str
    policy_revision: int
    subject_hash: str
    decided_at_epoch_ms: int


class PromptRolloutRepository(Protocol):
    async def get_policy(self, tenant_id: str, prompt_id: str) -> PromptRolloutPolicy | None: ...
    async def put_policy(self, policy: PromptRolloutPolicy) -> PromptRolloutPolicy: ...
    async def append_decision(self, decision: PromptRouteDecision) -> None: ...
    async def list_decisions(self, tenant_id: str, prompt_id: str, *, limit: int) -> list[PromptRouteDecision]: ...


class InMemoryPromptRolloutRepository:
    """Reference adapter for tests; production should inject PostgresPromptRolloutRepository."""

    def __init__(self) -> None:
        self._policies: dict[tuple[str, str], PromptRolloutPolicy] = {}
        self._decisions: list[PromptRouteDecision] = []
        import asyncio
        self._lock = asyncio.Lock()

    async def get_policy(self, tenant_id: str, prompt_id: str) -> PromptRolloutPolicy | None:
        async with self._lock:
            return self._policies.get((tenant_id, prompt_id))

    async def put_policy(self, policy: PromptRolloutPolicy) -> PromptRolloutPolicy:
        async with self._lock:
            key = (policy.tenant_id, policy.prompt_id)
            previous = self._policies.get(key)
            stored = PromptRolloutPolicy(
                policy.tenant_id, policy.prompt_id, policy.weights, policy.tag_routes,
                policy.tenant_routes, policy.updated_by,
                (previous.revision + 1) if previous else 1,
            )
            self._policies[key] = stored
            return stored

    async def append_decision(self, decision: PromptRouteDecision) -> None:
        async with self._lock:
            self._decisions.append(decision)

    async def list_decisions(self, tenant_id: str, prompt_id: str, *, limit: int) -> list[PromptRouteDecision]:
        async with self._lock:
            rows = [item for item in self._decisions
                    if item.tenant_id == tenant_id and item.prompt_id == prompt_id]
            return rows[-limit:]


class PostgresPromptRolloutRepository:
    """Durable policy and append-only decision adapter using the shared DB connection factory."""

    def __init__(self, connection_factory: Any) -> None:
        self._connect = connection_factory

    async def get_policy(self, tenant_id: str, prompt_id: str) -> PromptRolloutPolicy | None:
        scoped = tenant_prompt_key(tenant_id, prompt_id)
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT weights,tag_routes,tenant_routes,updated_by,revision
                   FROM platform_prompt_rollout_policies
                   WHERE tenant_id=%s AND prompt_id=%s""", (tenant_id, scoped),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return PromptRolloutPolicy(
            tenant_id, prompt_id, tuple((str(k), float(v)) for k, v in row["weights"].items()),
            tuple((str(k), str(v)) for k, v in row["tag_routes"].items()),
            tuple((str(k), str(v)) for k, v in row["tenant_routes"].items()),
            row["updated_by"], int(row["revision"]),
        )

    async def put_policy(self, policy: PromptRolloutPolicy) -> PromptRolloutPolicy:
        scoped = tenant_prompt_key(policy.tenant_id, policy.prompt_id)
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                variants = {name for name, _ in policy.weights}
                variants.update(value for _, value in policy.tag_routes)
                variants.update(value for _, value in policy.tenant_routes)
                for variant in sorted(variants):
                    cursor = await connection.execute(
                        """SELECT status FROM platform_prompt_variants
                           WHERE prompt_id=%s AND variant_name=%s FOR SHARE""",
                        (scoped, variant),
                    )
                    row = await cursor.fetchone()
                    if row is None or row["status"] != "active":
                        raise ValueError(f"variant is missing or inactive: {variant}")
                cursor = await connection.execute(
                    """INSERT INTO platform_prompt_rollout_policies
                       (tenant_id,prompt_id,weights,tag_routes,tenant_routes,updated_by,revision)
                       VALUES (%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,1)
                       ON CONFLICT (tenant_id,prompt_id) DO UPDATE SET
                         weights=EXCLUDED.weights,tag_routes=EXCLUDED.tag_routes,
                         tenant_routes=EXCLUDED.tenant_routes,updated_by=EXCLUDED.updated_by,
                         revision=platform_prompt_rollout_policies.revision+1,
                         updated_at=now()
                       RETURNING revision""",
                    (policy.tenant_id, scoped, json.dumps(dict(policy.weights), sort_keys=True),
                     json.dumps(dict(policy.tag_routes), sort_keys=True),
                     json.dumps(dict(policy.tenant_routes), sort_keys=True), policy.updated_by),
                )
                row = await cursor.fetchone()
                revision = int(row["revision"])
                await connection.execute(
                    """INSERT INTO platform_prompt_audit_events
                       (prompt_id,action,actor_id,details)
                       VALUES (%s,'rollout.policy_updated',%s,%s::jsonb)""",
                    (scoped, policy.updated_by, json.dumps({
                        "tenant_id": policy.tenant_id, "weights": dict(policy.weights),
                        "tag_routes": dict(policy.tag_routes),
                        "tenant_routes": dict(policy.tenant_routes), "revision": revision,
                    }, sort_keys=True)),
                )
        return PromptRolloutPolicy(
            policy.tenant_id, policy.prompt_id, policy.weights, policy.tag_routes,
            policy.tenant_routes, policy.updated_by, revision,
        )

    async def append_decision(self, decision: PromptRouteDecision) -> None:
        scoped = tenant_prompt_key(decision.tenant_id, decision.prompt_id)
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                await connection.execute(
                    """INSERT INTO platform_prompt_rollout_decisions
                       (tenant_id,prompt_id,variant_name,matched_rule,policy_revision,
                        subject_hash,decided_at)
                       VALUES (%s,%s,%s,%s,%s,%s,to_timestamp(%s / 1000.0))""",
                    (decision.tenant_id, scoped, decision.variant, decision.rule,
                     decision.policy_revision, decision.subject_hash,
                     decision.decided_at_epoch_ms),
                )
                await connection.execute(
                    """INSERT INTO platform_prompt_audit_events
                       (prompt_id,action,actor_id,details)
                       VALUES (%s,'rollout.route_decided','system',%s::jsonb)""",
                    (scoped, json.dumps({
                        "tenant_id": decision.tenant_id, "variant": decision.variant,
                        "rule": decision.rule, "policy_revision": decision.policy_revision,
                        "subject_hash": decision.subject_hash,
                    }, sort_keys=True)),
                )

    async def list_decisions(self, tenant_id: str, prompt_id: str, *, limit: int) -> list[PromptRouteDecision]:
        scoped = tenant_prompt_key(tenant_id, prompt_id)
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT tenant_id,variant_name,matched_rule,policy_revision,
                          subject_hash,decided_at
                   FROM platform_prompt_rollout_decisions
                   WHERE tenant_id=%s AND prompt_id=%s
                   ORDER BY decision_id DESC LIMIT %s""", (tenant_id, scoped, limit),
            )
            rows = await cursor.fetchall()
        return [PromptRouteDecision(
            row["tenant_id"], prompt_id, row["variant_name"], row["matched_rule"],
            int(row["policy_revision"]), row["subject_hash"],
            int(row["decided_at"].timestamp() * 1000),
        ) for row in rows]


class PromptRolloutService:
    def __init__(self, *, registry: Any, repository: PromptRolloutRepository) -> None:
        self.registry = registry
        self.repository = repository

    async def configure(
        self, *, tenant_id: str, prompt_id: str, weights: Mapping[str, float],
        actor: str, tag_routes: Mapping[str, str] | None = None,
        tenant_routes: Mapping[str, str] | None = None,
    ) -> PromptRolloutPolicy:
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("actor is required")
        if not isinstance(weights, Mapping) or not weights:
            raise ValueError("weights are required")
        scoped = tenant_prompt_key(tenant_id, prompt_id)
        variants = self.registry.list_variants(scoped)
        if inspect.isawaitable(variants):
            variants = await variants
        active = {item.name for item in variants if item.status == "active"}
        tag_routes = tag_routes or {}
        tenant_routes = tenant_routes or {}
        selected = set(weights) | set(tag_routes.values()) | set(tenant_routes.values())
        if not selected.issubset(active):
            raise ValueError("all rollout targets must be active variants in this tenant")
        policy = PromptRolloutPolicy(
            tenant_id, prompt_id, tuple(sorted((str(k), float(v)) for k, v in weights.items())),
            tuple(sorted((str(k), str(v)) for k, v in tag_routes.items())),
            tuple(sorted((str(k), str(v)) for k, v in tenant_routes.items())), actor,
        )
        return await self.repository.put_policy(policy)

    async def route(
        self, *, tenant_id: str, prompt_id: str, routing_key: str,
        user_tags: tuple[str, ...] = (), policy_tenant_id: str | None = None,
    ) -> PromptRouteDecision:
        if not isinstance(routing_key, str) or not routing_key.strip():
            raise ValueError("routing_key is required for deterministic routing")
        owner = policy_tenant_id or tenant_id
        policy = await self.repository.get_policy(owner, prompt_id)
        if policy is None:
            raise KeyError("prompt rollout policy does not exist")
        tenant_routes = dict(policy.tenant_routes)
        tag_routes = dict(policy.tag_routes)
        if tenant_id in tenant_routes:
            variant, rule = tenant_routes[tenant_id], "tenant"
        else:
            matched = sorted(set(user_tags) & set(tag_routes))
            if matched:
                variant, rule = tag_routes[matched[0]], f"tag:{matched[0]}"
            else:
                digest = hashlib.sha256(
                    f"{tenant_id}\0{prompt_id}\0{routing_key}".encode("utf-8")
                ).digest()
                bucket = int.from_bytes(digest[:8], "big") / 0xFFFFFFFFFFFFFFFF * 100
                cumulative = 0.0
                variant = policy.weights[-1][0]
                for name, weight in policy.weights:
                    cumulative += weight
                    if bucket < cumulative:
                        variant = name
                        break
                rule = "percentage"
        decision = PromptRouteDecision(
            tenant_id, prompt_id, variant, rule, policy.revision,
            hashlib.sha256(routing_key.encode("utf-8")).hexdigest(),
            int(time.time() * 1000),
        )
        await self.repository.append_decision(decision)
        return decision

    async def auto_promote(
        self, *, report: Any, actor: str, minimum_score_gain: float = 0.0,
    ) -> PromptRolloutPolicy:
        if not isinstance(minimum_score_gain, (int, float)) or isinstance(minimum_score_gain, bool) or not math.isfinite(minimum_score_gain) or minimum_score_gain < 0:
            raise ValueError("minimum_score_gain must be finite and non-negative")
        metrics = {item.variant: item for item in report.metrics}
        baseline = metrics.get(report.baseline_variant)
        if baseline is None or not metrics:
            raise ValueError("experiment report has no baseline metrics")
        candidates = [item for name, item in metrics.items() if name != report.baseline_variant]
        if not candidates:
            raise ValueError("experiment report has no candidate variants")
        winner = max(candidates, key=lambda item: (item.average_score, item.pass_rate, -item.average_latency_ms))
        if winner.average_score < baseline.average_score + minimum_score_gain:
            raise ValueError("no candidate meets the automatic promotion threshold")
        return await self.configure(
            tenant_id=report.tenant_id, prompt_id=report.prompt_id,
            weights={winner.variant: 100.0}, actor=actor,
        )

    async def manual_cutover(
        self, *, tenant_id: str, prompt_id: str, variant: str, actor: str,
    ) -> PromptRolloutPolicy:
        return await self.configure(
            tenant_id=tenant_id, prompt_id=prompt_id,
            weights={variant: 100.0}, actor=actor,
        )

    async def audit(
        self, *, tenant_id: str, prompt_id: str, limit: int = 100,
    ) -> list[PromptRouteDecision]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        return await self.repository.list_decisions(tenant_id, prompt_id, limit=limit)
