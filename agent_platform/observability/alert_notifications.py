"""Provider-neutral asynchronous delivery for durable alert outbox events."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from agent_platform.observability.alerts import AlertEvent


class AlertDeliveryStore(Protocol):
    async def claim_pending_deliveries(
        self, *, limit: int = 100, event_id: str | None = None
    ) -> list[dict[str, Any]]: ...
    async def mark_delivery(
        self, event_id: str, *, success: bool, now: datetime, error: str | None = None
    ) -> None: ...


class AlertNotificationProvider(Protocol):
    async def send(self, event: AlertEvent) -> None: ...


class AlertNotificationDispatcher:
    """Dispatch durable alert events through an injected production provider.

    Provider exceptions are reduced to their class name before persistence;
    credentials, endpoint response bodies and raw exception messages are not
    written to the notification ledger.
    """

    def __init__(
        self, *, store: AlertDeliveryStore, provider: AlertNotificationProvider,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.provider = provider
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    async def retry_pending(
        self, *, limit: int = 100, event_id: str | None = None
    ) -> dict[str, int]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer between 1 and 500")
        rows = await self.store.claim_pending_deliveries(limit=limit, event_id=event_id)
        result = {"claimed": len(rows), "sent": 0, "failed": 0}
        for row in rows:
            alert = AlertEvent(
                rule_id=row["rule_id"], metric_name=row["metric_name"],
                observed_value=row["observed_value"], threshold=row["threshold"],
                severity=row["severity"], fired_at=row["fired_at"],
                description=row["description"],
            )
            try:
                await self.provider.send(alert)
            except Exception as exc:
                await self.store.mark_delivery(
                    row["event_id"], success=False, now=self.clock(),
                    error=type(exc).__name__,
                )
                result["failed"] += 1
            else:
                await self.store.mark_delivery(
                    row["event_id"], success=True, now=self.clock(),
                )
                result["sent"] += 1
        return result
