"""Detect, decide, act, and audit runtime security events.

The service deliberately separates detection, policy decision, allow-listed
response execution, and audit. Durable event claiming is delegated to an
atomic store so duplicate deliveries cannot repeat a response action.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Awaitable, Callable, Mapping, Protocol

from agent_platform.observability.redaction import redact_payload


class SecuritySeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    HIGH = "high"
    CRITICAL = "critical"


_SEVERITY_ORDER = {
    SecuritySeverity.INFO: 0,
    SecuritySeverity.WARNING: 1,
    SecuritySeverity.HIGH: 2,
    SecuritySeverity.CRITICAL: 3,
}
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,255}$")


def _required_id(name: str, value: str) -> None:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(f"{name} must be a non-empty safe identifier")


@dataclass(frozen=True)
class RuntimeSecurityObservation:
    observation_id: str
    tenant_id: str
    run_id: str
    actor_id: str
    event_type: str
    signal: str
    observed_value: float
    occurred_at: str
    resource_id: str = ""
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("observation_id", "tenant_id", "run_id", "actor_id", "event_type", "signal"):
            _required_id(name, getattr(self, name))
        if self.resource_id:
            _required_id("resource_id", self.resource_id)
        if not isinstance(self.occurred_at, str) or not self.occurred_at.strip():
            raise ValueError("occurred_at is required")
        if isinstance(self.observed_value, bool) or not isinstance(self.observed_value, (int, float)) or not math.isfinite(self.observed_value):
            raise ValueError("observed_value must be finite")
        if not isinstance(self.attributes, Mapping):
            raise ValueError("attributes must be a mapping")


@dataclass(frozen=True)
class SecurityEvent:
    event_id: str
    tenant_id: str
    run_id: str
    actor_id: str
    event_type: str
    severity: SecuritySeverity
    signal: str
    observed_value: float
    occurred_at: str
    resource_id: str = ""
    attributes: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DetectionRule:
    event_type: str
    signal: str
    threshold: float
    comparison: str = "gte"
    severity: SecuritySeverity = SecuritySeverity.WARNING

    def __post_init__(self) -> None:
        _required_id("event_type", self.event_type)
        _required_id("signal", self.signal)
        if isinstance(self.threshold, bool) or not isinstance(self.threshold, (int, float)) or not math.isfinite(self.threshold):
            raise ValueError("threshold must be finite")
        if self.comparison not in {"gt", "gte", "lt", "lte"}:
            raise ValueError("unsupported comparison")


class SecurityEventDetector(Protocol):
    async def detect(self, observation: RuntimeSecurityObservation) -> SecurityEvent | None: ...


class ThresholdSecurityDetector:
    """Deterministic detector over normalized runtime telemetry observations."""

    def __init__(self, rules: tuple[DetectionRule, ...]) -> None:
        keys = [(r.event_type, r.signal) for r in rules]
        if len(keys) != len(set(keys)):
            raise ValueError("detection rule event_type/signal pairs must be unique")
        self._rules = {(r.event_type, r.signal): r for r in rules}

    async def detect(self, observation: RuntimeSecurityObservation) -> SecurityEvent | None:
        rule = self._rules.get((observation.event_type, observation.signal))
        if rule is None:
            return None
        value = float(observation.observed_value)
        matched = {
            "gt": value > rule.threshold,
            "gte": value >= rule.threshold,
            "lt": value < rule.threshold,
            "lte": value <= rule.threshold,
        }[rule.comparison]
        if not matched:
            return None
        return SecurityEvent(
            event_id=observation.observation_id,
            tenant_id=observation.tenant_id,
            run_id=observation.run_id,
            actor_id=observation.actor_id,
            event_type=observation.event_type,
            severity=rule.severity,
            signal=observation.signal,
            observed_value=value,
            occurred_at=observation.occurred_at,
            resource_id=observation.resource_id,
            attributes=redact_payload(dict(observation.attributes)),
        )


class ResponseAction(StrEnum):
    INTERRUPT_RUN = "interrupt_run"
    ISOLATE_RUN = "isolate_run"
    ISOLATE_TENANT = "isolate_tenant"
    REVOKE_CREDENTIAL = "revoke_credential"


@dataclass(frozen=True)
class ResponseRule:
    event_type: str
    minimum_severity: SecuritySeverity
    action: ResponseAction
    reason: str

    def __post_init__(self) -> None:
        _required_id("event_type", self.event_type)
        if not isinstance(self.minimum_severity, SecuritySeverity):
            raise ValueError("minimum_severity must be SecuritySeverity")
        if not isinstance(self.action, ResponseAction):
            raise ValueError("action must be an allow-listed ResponseAction")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason is required")


@dataclass(frozen=True)
class ResponseDecision:
    approved: bool
    action: ResponseAction | None
    reason: str
    policy_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.approved, bool):
            raise ValueError("approved must be bool")
        if self.approved and not isinstance(self.action, ResponseAction):
            raise ValueError("approved decisions require an allow-listed action")
        if not isinstance(self.policy_version, str) or not self.policy_version.strip():
            raise ValueError("policy_version is required")


class RuntimeSecurityPolicy(Protocol):
    async def decide(self, event: SecurityEvent) -> ResponseDecision: ...


class RuleBasedSecurityPolicy:
    """First exact event-type rule meeting the configured severity floor."""

    def __init__(self, rules: tuple[ResponseRule, ...], *, version: str) -> None:
        if not isinstance(version, str) or not version.strip():
            raise ValueError("policy version is required")
        self._rules = rules
        self.version = version

    async def decide(self, event: SecurityEvent) -> ResponseDecision:
        for rule in self._rules:
            if (
                rule.event_type == event.event_type
                and _SEVERITY_ORDER[event.severity] >= _SEVERITY_ORDER[rule.minimum_severity]
            ):
                return ResponseDecision(True, rule.action, rule.reason, self.version)
        return ResponseDecision(False, None, "no_matching_response_rule", self.version)


SecurityActionHandler = Callable[[SecurityEvent, str], Awaitable[None]]


class SecurityResponseExecutor:
    """Execute only explicitly registered response actions; no dynamic dispatch."""

    def __init__(self, handlers: Mapping[ResponseAction, SecurityActionHandler]) -> None:
        invalid = set(handlers) - set(ResponseAction)
        if invalid:
            raise ValueError("response handler contains an unsupported action")
        self._handlers = dict(handlers)

    async def execute(self, action: ResponseAction, event: SecurityEvent, reason: str) -> None:
        handler = self._handlers.get(action)
        if handler is None:
            raise RuntimeError(f"response action is not configured: {action.value}")
        await handler(event, reason)


class SecurityResponseAudit(Protocol):
    async def record(
        self, *, tenant_id: str, actor_id: str, action: str,
        subject_id: str, outcome: str = "succeeded",
        detail: Mapping[str, Any] | None = None,
    ) -> None: ...


class SecurityResponseEventStore(Protocol):
    async def claim_once(self, *, tenant_id: str, event_id: str) -> bool:
        """Atomically claim this tenant/event key; false means already handled."""
        ...

    async def complete(self, *, tenant_id: str, event_id: str, outcome: str) -> None: ...


@dataclass(frozen=True)
class SecurityResponseResult:
    status: str
    event_id: str | None = None
    action: str | None = None
    policy_version: str | None = None


class RuntimeSecurityAutoResponder:
    """Orchestrate detection -> policy decision -> response action -> audit."""

    SYSTEM_ACTOR = "system:runtime-security-responder"

    def __init__(
        self, *, detector: SecurityEventDetector, policy: RuntimeSecurityPolicy,
        executor: SecurityResponseExecutor, audit: SecurityResponseAudit,
        event_store: SecurityResponseEventStore,
    ) -> None:
        self.detector = detector
        self.policy = policy
        self.executor = executor
        self.audit = audit
        self.event_store = event_store

    async def handle(self, observation: RuntimeSecurityObservation) -> SecurityResponseResult:
        event = await self.detector.detect(observation)
        if event is None:
            return SecurityResponseResult("no_detection")
        if not await self.event_store.claim_once(tenant_id=event.tenant_id, event_id=event.event_id):
            return SecurityResponseResult("duplicate", event.event_id)

        try:
            decision = await self.policy.decide(event)
            if not isinstance(decision, ResponseDecision):
                raise TypeError("policy returned an invalid decision")
        except Exception as exc:
            await self._audit(
                event, "security.response.decision", "failed",
                {"error_type": type(exc).__name__},
            )
            await self.event_store.complete(
                tenant_id=event.tenant_id, event_id=event.event_id, outcome="decision_failed",
            )
            return SecurityResponseResult("decision_failed", event.event_id)

        decision_detail = {
            "event_type": event.event_type,
            "severity": event.severity.value,
            "signal": event.signal,
            "observed_value": event.observed_value,
            "policy_version": decision.policy_version,
            "approved": decision.approved,
            "response_action": decision.action.value if decision.action else None,
            "reason": decision.reason,
        }
        await self._audit(
            event, "security.response.decision",
            "succeeded" if decision.approved else "denied", decision_detail,
        )
        if not decision.approved:
            await self.event_store.complete(
                tenant_id=event.tenant_id, event_id=event.event_id, outcome="no_action",
            )
            return SecurityResponseResult(
                "no_action", event.event_id, policy_version=decision.policy_version,
            )

        # ResponseDecision validates this invariant at construction; assert it here
        # so static type checkers also know an approved action is non-null.
        assert decision.action is not None

        # Write-ahead audit is mandatory before an isolation/interrupt/revoke side effect.
        try:
            await self._audit(event, "security.response.action", "started", decision_detail)
        except Exception:
            await self.event_store.complete(
                tenant_id=event.tenant_id, event_id=event.event_id, outcome="audit_blocked",
            )
            return SecurityResponseResult(
                "audit_blocked", event.event_id, decision.action.value,
                decision.policy_version,
            )

        try:
            await self.executor.execute(decision.action, event, decision.reason)
        except Exception as exc:
            try:
                await self._audit(
                    event, "security.response.action", "failed",
                    {**decision_detail, "error_type": type(exc).__name__},
                )
            finally:
                await self.event_store.complete(
                    tenant_id=event.tenant_id, event_id=event.event_id, outcome="action_failed",
                )
            return SecurityResponseResult(
                "action_failed", event.event_id, decision.action.value,
                decision.policy_version,
            )

        await self._audit(event, "security.response.action", "succeeded", decision_detail)
        await self.event_store.complete(
            tenant_id=event.tenant_id, event_id=event.event_id, outcome="action_succeeded",
        )
        return SecurityResponseResult(
            "action_succeeded", event.event_id, decision.action.value,
            decision.policy_version,
        )

    async def _audit(
        self, event: SecurityEvent, action: str, outcome: str,
        detail: Mapping[str, Any],
    ) -> None:
        await self.audit.record(
            tenant_id=event.tenant_id,
            actor_id=self.SYSTEM_ACTOR,
            action=action,
            subject_id=event.event_id,
            outcome=outcome,
            detail=redact_payload(dict(detail)),
        )
