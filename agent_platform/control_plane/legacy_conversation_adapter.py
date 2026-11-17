"""Read-only compatibility mapper for explicit legacy conversation imports.

This module never reads or writes legacy tables and never persists adapted objects.
Callers must supply an approved tenant mapping and, when applicable, project mapping.
"""
from __future__ import annotations

from collections.abc import Mapping
from uuid import NAMESPACE_URL, uuid5

from agent_platform.control_plane.sessions_assets import (
    ConversationMessage,
    ConversationSession,
    MessageRole,
)


class LegacyMappingError(ValueError):
    """Legacy row cannot be mapped without guessing tenant/project ownership."""


class LegacyConversationAdapter:
    """Deterministically map legacy row dictionaries into canonical domain values.

    The adapter is intentionally pure/read-only: it has no repository, database,
    storage, or dual-write dependency. A separate migration owns persistence.
    """

    @staticmethod
    def _stable_id(tenant_id: str, kind: str, source_id: str) -> str:
        return str(uuid5(NAMESPACE_URL, f"agent-pingtai:legacy:{tenant_id}:{kind}:{source_id}"))

    def adapt_session(
        self, row: Mapping[str, object], *, tenant_id: str,
        approved_owner_id: str,
        approved_project_id: str | None = None,
    ) -> ConversationSession:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise LegacyMappingError("an explicit tenant mapping is required")
        source_id = row.get("conversation_id") or row.get("id")
        if not isinstance(source_id, str) or not source_id.strip():
            raise LegacyMappingError("legacy conversation identifier is required")
        if not isinstance(approved_owner_id, str) or not approved_owner_id.strip():
            raise LegacyMappingError("an explicit approved platform owner mapping is required")
        legacy_project = row.get("project_id")
        if legacy_project is not None and not approved_project_id:
            raise LegacyMappingError("legacy project requires an explicit approved project mapping")
        title = row.get("title") or ""
        if not isinstance(title, str):
            raise LegacyMappingError("legacy title must be text")
        status = row.get("status") or "active"
        if status not in {"active", "archived", "deleted"}:
            status = "archived"
        return ConversationSession(
            tenant_id=tenant_id,
            session_id=self._stable_id(tenant_id, "session", source_id),
            owner_id=approved_owner_id,
            title=title[:200],
            project_id=approved_project_id,
            status=str(status),
        )

    def adapt_message(
        self, row: Mapping[str, object], *, tenant_id: str,
        session_id: str, actor_id: str,
    ) -> ConversationMessage:
        if not tenant_id.strip() or not session_id.strip() or not actor_id.strip():
            raise LegacyMappingError("explicit tenant, target session and actor mappings are required")
        source_id = row.get("message_id") or row.get("id")
        content = row.get("content") or row.get("message")
        role_value = row.get("role") or "user"
        if not isinstance(source_id, str) or not source_id.strip():
            raise LegacyMappingError("legacy message identifier is required")
        if not isinstance(content, str):
            raise LegacyMappingError("legacy message content must be text")
        try:
            role = MessageRole(str(role_value).lower())
        except ValueError as exc:
            raise LegacyMappingError("legacy message role requires an explicit mapping") from exc
        return ConversationMessage(
            tenant_id=tenant_id,
            message_id=self._stable_id(tenant_id, "message", source_id),
            session_id=session_id,
            role=role,
            content=content,
            actor_id=actor_id,
            metadata={"legacy_source": "conversation", "legacy_id": source_id},
        )
