"""Ports for identity persistence and policy decisions."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from agent_platform.identity.models import SessionRecord, UserRecord


class IdentityRepository(Protocol):
    async def get_user_by_email(
        self, normalized_email: str
    ) -> UserRecord | None:
        ...

    async def is_active_member(self, *, tenant_id: str, user_id: str) -> bool:
        ...

    async def create_session(
        self, *, session_id: str, tenant_id: str, user_id: str,
        token_hash: str, expires_at: datetime,
    ) -> None:
        ...

    async def get_session_by_token_hash(
        self, token_hash: str
    ) -> SessionRecord | None:
        ...

    async def touch_session(self, *, session_id: str, tenant_id: str) -> None:
        ...

    async def revoke_session(
        self, *, session_id: str, tenant_id: str, user_id: str,
        revoked_at: datetime,
    ) -> bool:
        ...

    async def has_permission(
        self, *, tenant_id: str, user_id: str, resource_type: str,
        resource_id: str, permission_key: str,
    ) -> bool:
        ...

    async def grant_role(
        self, *, tenant_id: str, actor_id: str, subject_type: str,
        subject_id: str, resource_type: str, resource_id: str, role_id: str,
    ) -> str:
        ...

    async def revoke_role(
        self, *, tenant_id: str, actor_id: str, binding_id: str,
    ) -> bool:
        ...
