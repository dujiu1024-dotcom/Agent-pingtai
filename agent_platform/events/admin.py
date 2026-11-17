"""Tenant-scoped webhook subscription administration and secret lifecycle."""
from __future__ import annotations

import inspect
from typing import Any, Protocol
from uuid import uuid4

from agent_platform.events.webhooks import WebhookSubscription


class WebhookAdminStore(Protocol):
    async def create_subscription(self, subscription: WebhookSubscription, *, created_by: str) -> None: ...
    async def list_subscriptions(self, tenant_id: str) -> list[dict[str, Any]]: ...
    async def update_secret_ref(self, tenant_id: str, subscription_id: str, *, secret_ref: str) -> str | None: ...
    async def disable_subscription(self, tenant_id: str, subscription_id: str) -> str | None: ...
    async def list_pending_secret_revocations(self, *, limit: int = 100) -> list[dict[str, str]]: ...
    async def complete_secret_revocation(self, tenant_id: str, subscription_id: str, *, secret_ref: str) -> bool: ...


class WebhookSecretManager(Protocol):
    def store(self, *, tenant_id: str, secret: bytes) -> Any: ...
    def revoke(self, *, secret_ref: str) -> Any: ...


async def _await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


class WebhookSubscriptionService:
    def __init__(self, *, store: WebhookAdminStore, secrets: WebhookSecretManager,
                 revocation_lease_seconds: int = 60) -> None:
        if isinstance(revocation_lease_seconds, bool) or not 5 <= revocation_lease_seconds <= 3600:
            raise ValueError("revocation_lease_seconds must be between 5 and 3600")
        self.store = store
        self.secrets = secrets
        self.revocation_lease_seconds = revocation_lease_seconds

    async def create(self, *, tenant_id: str, actor_id: str, url: str,
                     topics: tuple[str, ...], signing_secret: bytes) -> str:
        if not tenant_id or not actor_id:
            raise ValueError("tenant and actor are required")
        normalized_topics = tuple(dict.fromkeys(topic.strip() for topic in topics if topic.strip()))
        if not normalized_topics or len(normalized_topics) != len(topics):
            raise ValueError("topics must be non-empty unique strings")
        if len(signing_secret) < 32:
            raise ValueError("signing secret must contain at least 32 bytes")
        secret_ref = await _await(self.secrets.store(tenant_id=tenant_id, secret=signing_secret))
        if not isinstance(secret_ref, str) or not secret_ref:
            raise RuntimeError("secret manager did not return a secret reference")
        try:
            subscription = WebhookSubscription(
                subscription_id=str(uuid4()), tenant_id=tenant_id, url=url,
                secret_ref=secret_ref, topics=normalized_topics,
            )
            await self.store.create_subscription(subscription, created_by=actor_id)
        except BaseException:
            try:
                await _await(self.secrets.revoke(secret_ref=secret_ref))
            except Exception:
                pass
            raise
        return subscription.subscription_id

    async def list(self, *, tenant_id: str) -> list[dict[str, Any]]:
        if not tenant_id:
            raise ValueError("tenant is required")
        return await self.store.list_subscriptions(tenant_id)

    async def rotate(self, *, tenant_id: str, subscription_id: str,
                     signing_secret: bytes) -> bool:
        if len(signing_secret) < 32:
            raise ValueError("signing secret must contain at least 32 bytes")
        new_ref = await _await(self.secrets.store(tenant_id=tenant_id, secret=signing_secret))
        if not isinstance(new_ref, str) or not new_ref:
            raise RuntimeError("secret manager did not return a secret reference")
        try:
            old_ref = await self.store.update_secret_ref(
                tenant_id, subscription_id, secret_ref=new_ref,
            )
        except BaseException:
            try:
                await _await(self.secrets.revoke(secret_ref=new_ref))
            except Exception:
                pass
            raise
        if old_ref is None:
            await _await(self.secrets.revoke(secret_ref=new_ref))
            return False
        if old_ref != new_ref:
            # The old reference is already durable in the revocation table.
            # All revocation side effects go through the leased worker path.
            await self.retry_pending_secret_revocations(limit=100, raise_on_failure=True)
        return True

    async def _claim_pending_secret_revocations(self, *, limit: int) -> list[dict[str, str]]:
        # Production PostgresWebhookStore exposes its connection factory. Claiming
        # and lease-token rotation happen in one transaction with SKIP LOCKED.
        connect = getattr(self.store, "_connect", None)
        if connect is None:
            # Compatibility for lightweight test doubles; production must use SQL.
            return await self.store.list_pending_secret_revocations(limit=limit)
        connection = await connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """WITH due AS (
                         SELECT tenant_id,subscription_id,secret_ref
                         FROM platform_webhook_secret_revocations
                         WHERE lease_until IS NULL OR lease_until <= now()
                         ORDER BY created_at
                         LIMIT %s
                         FOR UPDATE SKIP LOCKED
                       )
                       UPDATE platform_webhook_secret_revocations r
                       SET lease_token=gen_random_uuid(),
                           lease_until=now()+make_interval(secs => %s),
                           lease_attempts=r.lease_attempts+1
                       FROM due
                       WHERE r.tenant_id=due.tenant_id
                         AND r.subscription_id=due.subscription_id
                         AND r.secret_ref=due.secret_ref
                       RETURNING r.tenant_id,r.subscription_id,r.secret_ref,r.lease_token""",
                    (limit, self.revocation_lease_seconds),
                )
                rows = await cursor.fetchall()
        return [{
            "tenant_id": row["tenant_id"],
            "subscription_id": str(row["subscription_id"]),
            "secret_ref": row["secret_ref"],
            "lease_token": str(row["lease_token"]),
        } for row in rows]

    async def _complete_claimed_secret_revocation(self, item: dict[str, str]) -> bool:
        connect = getattr(self.store, "_connect", None)
        if connect is None:
            return await self.store.complete_secret_revocation(
                item["tenant_id"], item["subscription_id"], secret_ref=item["secret_ref"],
            )
        connection = await connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """DELETE FROM platform_webhook_secret_revocations
                       WHERE tenant_id=%s AND subscription_id=%s AND secret_ref=%s
                         AND lease_token=%s AND lease_until > now()
                       RETURNING secret_ref""",
                    (item["tenant_id"], item["subscription_id"], item["secret_ref"],
                     item["lease_token"]),
                )
                return await cursor.fetchone() is not None

    async def retry_pending_secret_revocations(self, *, limit: int = 100,
                                              raise_on_failure: bool = False) -> dict[str, int]:
        if isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        pending = await self._claim_pending_secret_revocations(limit=limit)
        result = {"claimed": len(pending), "revoked": 0, "failed": 0}
        for item in pending:
            try:
                await _await(self.secrets.revoke(secret_ref=item["secret_ref"]))
            except Exception:
                result["failed"] += 1
                if raise_on_failure:
                    raise
                continue
            if await self._complete_claimed_secret_revocation(item):
                result["revoked"] += 1
            else:
                # A reclaimed/expired lease fences this worker from deleting
                # the new owner's queue row. Secret revoke remains idempotent.
                result["failed"] += 1
        return result

    async def disable(self, *, tenant_id: str, subscription_id: str) -> bool:
        old_ref = await self.store.disable_subscription(tenant_id, subscription_id)
        if old_ref is None:
            return False
        await self.retry_pending_secret_revocations(limit=100, raise_on_failure=True)
        return True
