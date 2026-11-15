"""Ports for identity persistence and policy decisions."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from agent_platform.identity.models import (
    ApiCredentialRecord,
    InvitationRecord,
    SessionRecord,
    UserRecord,
)


class IdentityRepository(Protocol):
    async def get_user_by_email(self, normalized_email: str) -> UserRecord | None: ...
    async def get_authorization_context(self, *, tenant_id: str, user_id: str): ...
    async def is_active_member(self, *, tenant_id: str, user_id: str) -> bool: ...
    async def create_session(
        self, *, session_id: str, tenant_id: str, user_id: str,
        token_hash: str, expires_at: datetime,
    ) -> None: ...
    async def get_session_by_token_hash(self, token_hash: str) -> SessionRecord | None: ...
    async def touch_session(self, *, session_id: str, tenant_id: str) -> bool: ...
    async def revoke_session(
        self, *, session_id: str, tenant_id: str, user_id: str,
        revoked_at: datetime,
    ) -> bool: ...
    async def has_permission(
        self, *, tenant_id: str, user_id: str, resource_type: str,
        resource_id: str, permission_key: str,
    ) -> bool: ...
    async def can_assign_role(
        self, *, tenant_id: str, actor_id: str, role_id: str,
    ) -> bool: ...
    async def grant_role(
        self, *, tenant_id: str, actor_id: str, subject_type: str,
        subject_id: str, resource_type: str, resource_id: str, role_id: str,
    ) -> str: ...
    async def revoke_role(
        self, *, tenant_id: str, actor_id: str, binding_id: str,
        resource_type: str, resource_id: str,
    ) -> bool: ...
    async def create_api_credential(
        self, *, credential_id: str, tenant_id: str, user_id: str, name: str,
        token_hash: str, scopes: tuple[str, ...], expires_at: datetime | None,
    ) -> None: ...
    async def get_api_credential(self, token_hash: str) -> ApiCredentialRecord | None: ...
    async def touch_api_credential(self, *, credential_id: str, tenant_id: str) -> bool: ...
    async def revoke_api_credential(
        self, *, credential_id: str, tenant_id: str, user_id: str,
    ) -> bool: ...
    async def rotate_api_credential(
        self, *, old_credential_id: str, new_credential_id: str,
        tenant_id: str, user_id: str, name: str, token_hash: str,
        scopes: tuple[str, ...], expires_at: datetime | None,
    ) -> bool: ...
    async def create_invitation(
        self, *, invitation_id: str, tenant_id: str, email_normalized: str,
        role_id: str, token_hash: str, invited_by: str, expires_at: datetime,
    ) -> None: ...
    async def get_invitation(self, token_hash: str) -> InvitationRecord | None: ...
    async def accept_invitation(
        self, *, invitation_id: str, token_hash: str, tenant_id: str,
        user_id: str, email_normalized: str,
    ) -> bool: ...
