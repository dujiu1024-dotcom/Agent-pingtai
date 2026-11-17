"""Durable, retryable alert notification dispatch with write-ahead audit."""
from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any, Protocol

from agent_platform.observability.alerts import AlertEvent
from agent_platform.observability.redaction import redact_text


class AlertEventStore(Protocol):
    async def record_once(self, event: AlertEvent, *, scope_key: str, dedupe_key: str) -> tuple[str, bool]: ...
    async def claim_pending_deliveries(self, *, limit: int = 100, event_id: str | None = None) -> list[dict[str, Any]]: ...
    async def mark_delivery(self, event_id: str, *, success: bool, now: datetime, error: str | None = None) -> None: ...


class AlertNotificationPort(Protocol):
    def notify(self, event: AlertEvent) -> Any: ...


class AlertAuditSink(Protocol):
    async def record(
        self, *, tenant_id: str, actor_id: str, action: str,
        subject_id: str, outcome: str = "succeeded",
        detail: dict[str, Any] | None = None,
    ) -> None: ...


class AlertDispatchService:
    """Persist and audit before notifying; audit failure prevents delivery."""

    SYSTEM_ACTOR_ID = "system:legacy-alert-dispatch"

    def __init__(
        self, store: AlertEventStore, notifier: AlertNotificationPort, *,
        audit_sink: AlertAuditSink, clock: Any | None = None,
    ) -> None:
        if not callable(getattr(audit_sink, "record", None)):
            raise TypeError("legacy alert dispatch requires an audit sink with record()")
        self.store = store
        self.notifier = notifier
        self.audit_sink = audit_sink
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    async def dispatch(self, event: AlertEvent, *, scope_key: str, dedupe_key: str) -> str:
        event_id, _created = await self.store.record_once(event, scope_key=scope_key, dedupe_key=dedupe_key)
        claimed = await self.store.claim_pending_deliveries(limit=1, event_id=event_id)
        if claimed:
            await self._deliver(claimed[0])
        return event_id

    async def retry_pending(self, *, limit: int = 100) -> dict[str, int]:
        claimed = await self.store.claim_pending_deliveries(limit=limit)
        sent = failed = 0
        for row in claimed:
            if await self._deliver(row):
                sent += 1
            else:
                failed += 1
        return {"claimed": len(claimed), "sent": sent, "failed": failed}

    async def _deliver(self, row: dict[str, Any]) -> bool:
        event_id = str(row["event_id"])
        tenant_id = row.get("scope_key")
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            await self.store.mark_delivery(
                event_id, success=False, now=self.clock(), error="MissingTenantScope",
            )
            return False

        detail = {
            "event_id": event_id,
            "rule_id": str(row["rule_id"]),
            "metric_name": str(row["metric_name"]),
            "severity": str(row["severity"]),
            "delivery_attempts": row.get("delivery_attempts"),
        }
        # Write-ahead audit is mandatory: no external notification is sent if
        # the audit ledger cannot durably record the attempt.
        try:
            await self.audit_sink.record(
                tenant_id=tenant_id,
                actor_id=self.SYSTEM_ACTOR_ID,
                action="alert.dispatch.attempt",
                subject_id=event_id,
                outcome="succeeded",
                detail=detail,
            )
        except Exception:
            await self.store.mark_delivery(
                event_id, success=False, now=self.clock(), error="AuditUnavailable",
            )
            return False

        event = AlertEvent(
            rule_id=str(row["rule_id"]), metric_name=str(row["metric_name"]),
            observed_value=float(row["observed_value"]), threshold=float(row["threshold"]),
            severity=str(row["severity"]), fired_at=row["fired_at"],
            description=str(row.get("description") or ""),
        )
        try:
            result = self.notifier.notify(event)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            await self.store.mark_delivery(
                event_id, success=False, now=self.clock(),
                error=redact_text(f"{type(exc).__name__}: {exc}", max_chars=1000),
            )
            try:
                await self.audit_sink.record(
                    tenant_id=tenant_id,
                    actor_id=self.SYSTEM_ACTOR_ID,
                    action="alert.dispatch.delivery",
                    subject_id=event_id,
                    outcome="failed",
                    detail={**detail, "error_type": type(exc).__name__},
                )
            except Exception:
                # The write-ahead record remains durable; delivery remains retryable.
                pass
            return False

        await self.store.mark_delivery(event_id, success=True, now=self.clock())
        try:
            await self.audit_sink.record(
                tenant_id=tenant_id,
                actor_id=self.SYSTEM_ACTOR_ID,
                action="alert.dispatch.delivery",
                subject_id=event_id,
                outcome="succeeded",
                detail=detail,
            )
        except Exception:
            # Delivery is already complete. Keep it marked sent to avoid a
            # duplicate external side effect; the missing result audit is visible
            # as an incomplete attempt in the append-only audit ledger.
            return False
        return True
