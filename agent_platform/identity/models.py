"""Identity and authorization value objects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

UserStatus = Literal["active", "suspended", "disabled"]


@dataclass(frozen=True)
class UserRecord:
    user_id: str
    email: str
    display_name: str
    password_hash: str
    status: UserStatus


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    tenant_id: str
    user_id: str
    email: str
    display_name: str
    expires_at: datetime
    revoked_at: datetime | None
    user_status: UserStatus
    membership_status: str


@dataclass(frozen=True)
class Principal:
    user_id: str
    tenant_id: str
    session_id: str
    email: str
    display_name: str


@dataclass(frozen=True)
class IssuedSession:
    session_id: str
    tenant_id: str
    user_id: str
    access_token: str
    expires_at: datetime


@dataclass(frozen=True)
class ResourceRef:
    resource_type: str
    resource_id: str

    def __post_init__(self) -> None:
        if not self.resource_type.strip() or not self.resource_id.strip():
            raise ValueError("resource_type and resource_id must not be empty")
