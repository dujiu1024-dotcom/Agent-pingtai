"""Fail-closed mapping from external sharing permissions to platform ACLs.

The host supplies the identity resolver because external directory IDs are not
platform user/role/project IDs. Unsupported public/link/domain grants are rejected
instead of silently widening access.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

ACLGrant = dict[str, str]
PrincipalResolver = Callable[
    [str, str, str | None], tuple[str, str] | None
]


class ACLMappingError(ValueError):
    """An external sharing rule cannot be represented by the platform ACL model."""


def _resolve(
    resolver: PrincipalResolver, provider: str, subject_type: str,
    subject_id: str, email: str | None, permission: str,
) -> ACLGrant:
    mapped = resolver(provider, subject_type, email or subject_id)
    if (
        not isinstance(mapped, tuple) or len(mapped) != 2
        or not isinstance(mapped[0], str) or mapped[0] not in {"user", "role", "project"}
        or not isinstance(mapped[1], str) or not mapped[1].strip()
    ):
        raise ACLMappingError(
            f"{provider} {subject_type} permission has no platform identity mapping"
        )
    return {
        "principal_type": mapped[0],
        "principal_id": mapped[1],
        "permission": permission,
    }


def _deduplicate(grants: Sequence[ACLGrant]) -> tuple[ACLGrant, ...]:
    merged: dict[tuple[str, str], str] = {}
    for grant in grants:
        key = (grant["principal_type"], grant["principal_id"])
        previous = merged.get(key)
        if previous == "manage" or grant["permission"] == "manage":
            merged[key] = "manage"
        else:
            merged[key] = "read"
    return tuple(
        {
            "principal_type": kind,
            "principal_id": identifier,
            "permission": permission,
        }
        for (kind, identifier), permission in sorted(merged.items())
    )


def map_google_drive_permissions(
    permissions: Sequence[Mapping[str, Any]],
    resolver: PrincipalResolver,
) -> tuple[ACLGrant, ...]:
    """Map Drive user/group permissions; public/domain shares fail closed."""
    grants: list[ACLGrant] = []
    role_map = {
        "reader": "read",
        "commenter": "read",
        "writer": "manage",
        "fileorganizer": "manage",
        "organizer": "manage",
        "owner": "manage",
    }
    for item in permissions:
        if not isinstance(item, Mapping):
            raise ACLMappingError("Google Drive permission must be an object")
        if item.get("deleted") is True:
            continue
        subject_type = item.get("type")
        if not isinstance(subject_type, str) or subject_type not in {"user", "group"}:
            raise ACLMappingError(
                "Google Drive domain or anyone sharing cannot be mapped safely"
            )
        subject_id = item.get("id")
        email = item.get("emailAddress")
        if not isinstance(subject_id, str) or not subject_id.strip():
            if not isinstance(email, str) or not email.strip():
                raise ACLMappingError("Google Drive permission has no stable subject ID")
            subject_id = email
        role = item.get("role")
        if not isinstance(role, str) or role.casefold() not in role_map:
            raise ACLMappingError("Google Drive permission role is unsupported")
        grants.append(_resolve(
            resolver, "google-drive", subject_type, subject_id,
            email if isinstance(email, str) else None, role_map[role.casefold()],
        ))
    return _deduplicate(grants)


def map_sharepoint_permissions(
    permissions: Sequence[Mapping[str, Any]],
    resolver: PrincipalResolver,
) -> tuple[ACLGrant, ...]:
    """Map Graph user/group permissions; anonymous or organization links fail closed."""
    grants: list[ACLGrant] = []
    for item in permissions:
        if not isinstance(item, Mapping):
            raise ACLMappingError("Microsoft Graph permission must be an object")
        roles = item.get("roles")
        if not isinstance(roles, list) or not roles:
            raise ACLMappingError("Microsoft Graph permission is missing roles")
        normalized_roles = {role.casefold() for role in roles if isinstance(role, str)}
        if normalized_roles == {"read"}:
            permission = "read"
        elif normalized_roles and normalized_roles <= {"read", "write", "owner"} and normalized_roles & {"write", "owner"}:
            permission = "manage"
        else:
            raise ACLMappingError("Microsoft Graph sharing role is unsupported")
        identities: list[Mapping[str, Any]] = []
        for key in ("grantedToV2", "grantedTo"):
            identity = item.get(key)
            if isinstance(identity, Mapping):
                identities.append(identity)
        for key in ("grantedToIdentitiesV2", "grantedToIdentities"):
            values = item.get(key)
            if isinstance(values, list):
                identities.extend(value for value in values if isinstance(value, Mapping))
        if not identities:
            raise ACLMappingError(
                "Microsoft Graph anonymous or link permission cannot be mapped safely"
            )
        for identity in identities:
            subject_type = None
            subject_id = None
            email = None
            for key in ("user", "group", "siteUser", "siteGroup"):
                subject = identity.get(key)
                if isinstance(subject, Mapping):
                    subject_type = "group" if key in {"group", "siteGroup"} else "user"
                    subject_id = subject.get("id")
                    email = subject.get("email")
                    break
            if subject_type is None or not isinstance(subject_id, str) or not subject_id.strip():
                raise ACLMappingError("Microsoft Graph identity is not a resolvable user or group")
            grants.append(_resolve(
                resolver, "sharepoint", subject_type, subject_id,
                email if isinstance(email, str) else None, permission,
            ))
    return _deduplicate(grants)
