"""Provider-neutral asynchronous delivery for durable alert outbox events."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Protocol
from urllib.parse import urlsplit

from agent_platform.observability.alerts import AlertEvent
from agent_platform.observability.redaction import redact_payload


class AlertDeliveryStore(Protocol):
    async def claim_pending_deliveries(
        self, *, limit: int = 100, event_id: str | None = None
    ) -> list[dict[str, Any]]: ...
    async def mark_delivery(
        self, event_id: str, *, success: bool, now: datetime, error: str | None = None
    ) -> None: ...


class AlertNotificationProvider(Protocol):
    async def send(self, event: AlertEvent) -> None: ...


class AlertWebhookClient(Protocol):
    async def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: float) -> Any: ...


class WebhookAlertNotificationProvider:
    """Send alerts to one explicitly allowlisted HTTPS endpoint."""

    def __init__(
        self,
        *,
        client: AlertWebhookClient,
        endpoint: str,
        allowed_hosts: tuple[str, ...],
        authorization_header: str | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        if isinstance(allowed_hosts, str) or not allowed_hosts:
            raise ValueError("an explicit webhook host allowlist is required")
        hosts = {host.lower().strip() for host in allowed_hosts}
        if any(not host or "/" in host or ":" in host for host in hosts):
            raise ValueError("allowed webhook hosts must be bare hostnames")
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.hostname.lower() not in hosts
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.port not in (None, 443)
        ):
            raise ValueError("webhook endpoint must be HTTPS on an explicitly allowed host")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not 0.1 <= timeout_seconds <= 30
        ):
            raise ValueError("timeout_seconds must be between 0.1 and 30")
        if authorization_header is not None and (
            not isinstance(authorization_header, str)
            or not authorization_header.strip()
            or "\r" in authorization_header
            or "\n" in authorization_header
        ):
            raise ValueError("authorization_header must be a safe non-empty header value")
        self.client = client
        self.endpoint = endpoint
        self.authorization_header = authorization_header
        self.timeout_seconds = float(timeout_seconds)

    async def send(self, event: AlertEvent) -> None:
        if not isinstance(event, AlertEvent):
            raise TypeError("event must be an AlertEvent")
        payload = redact_payload({
            "source": "agent-platform",
            "rule_id": event.rule_id,
            "metric_name": event.metric_name,
            "observed_value": event.observed_value,
            "threshold": event.threshold,
            "severity": event.severity,
            "fired_at": event.fired_at.isoformat(),
            "description": event.description[:1000],
        })
        headers = {"content-type": "application/json"}
        if self.authorization_header is not None:
            headers["authorization"] = self.authorization_header
        response = await self.client.post(
            self.endpoint,
            json=payload,
            headers=headers,
            timeout=self.timeout_seconds,
        )
        status = getattr(response, "status_code", None)
        if isinstance(status, bool) or not isinstance(status, int) or not 200 <= status < 300:
            raise RuntimeError("alert webhook provider returned a non-success status")


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
