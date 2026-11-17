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


class WebhookSecretManager(Protocol):
    def store(self, *, tenant_id: str, secret: bytes) -> Any: ...
    def revoke(self, *, secret_ref: str) -> Any: ...


async def _await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


class WebhookSubscriptionService:
    def __init__(self, *, store: WebhookAdminStore, secrets: WebhookSecretManager) -> None:
        self.store = store
        self.secrets = secrets

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
            await _await(self.secrets.revoke(secret_ref=old_ref))
        return True

    async def disable(self, *, tenant_id: str, subscription_id: str) -> bool:
        old_ref = await self.store.disable_subscription(tenant_id, subscription_id)
        if old_ref is None:
            return False
        await _await(self.secrets.revoke(secret_ref=old_ref))
        return True
