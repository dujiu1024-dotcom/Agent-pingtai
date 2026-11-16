"""Durable, retryable alert notification dispatch."""
from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any, Protocol

from agent_platform.observability.alerts import AlertEvent
from agent_platform.observability.redaction import redact_text


class AlertEventStore(Protocol):
    async def record_once(
        self, event: AlertEvent, *, scope_key: str, dedupe_key: str
    ) -> tuple[str, bool]: ...
    async def claim_pending_deliveries(
        self, *, limit: int = 100, event_id: str | None = None
    ) -> list[dict[str, Any]]: ...
    async def mark_delivery(
        self, event_id: str, *, success: bool, now: datetime, error: str | None = None
    ) -> None: ...


class AlertNotificationPort(Protocol):
    def notify(self, event: AlertEvent) -> Any: ...


class AlertDispatchService:
    """Persist before notifying; failed deliveries remain retryable in PostgreSQL."""

    def __init__(
        self, store: AlertEventStore, notifier: AlertNotificationPort, *,
        clock: Any | None = None,
    ) -> None:
        self.store = store
        self.notifier = notifier
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    async def dispatch(
        self, event: AlertEvent, *, scope_key: str, dedupe_key: str
    ) -> str:
        event_id, _created = await self.store.record_once(
            event, scope_key=scope_key, dedupe_key=dedupe_key
        )
        claimed = await self.store.claim_pending_deliveries(limit=1, event_id=event_id)
        if claimed:
            await self._deliver(claimed[0])
        return event_id

    async def retry_pending(self, *, limit: int = 100) -> dict[str, int]:
        claimed = await self.store.claim_pending_deliveries(limit=limit)
        sent = failed = 0
        for row in claimed:
            success = await self._deliver(row)
            if success:
                sent += 1
            else:
                failed += 1
        return {"claimed": len(claimed), "sent": sent, "failed": failed}

    async def _deliver(self, row: dict[str, Any]) -> bool:
        event = AlertEvent(
            rule_id=str(row["rule_id"]),
            metric_name=str(row["metric_name"]),
            observed_value=float(row["observed_value"]),
            threshold=float(row["threshold"]),
            severity=str(row["severity"]),
            fired_at=row["fired_at"],
            description=str(row.get("description") or ""),
        )
        try:
            result = self.notifier.notify(event)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            await self.store.mark_delivery(
                str(row["event_id"]), success=False, now=self.clock(),
                error=redact_text(f"{type(exc).__name__}: {exc}", max_chars=1000),
            )
            return False
        await self.store.mark_delivery(
            str(row["event_id"]), success=True, now=self.clock()
        )
        return True
