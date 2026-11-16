"""Connector contract for incremental knowledge ingestion and authorization revocation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from agent_platform.knowledge.contracts import Document


@dataclass(frozen=True)
class ConnectorDocument:
    document: Document
    content: bytes
    cursor: str | None = None


@dataclass(frozen=True)
class ConnectorPage:
    items: tuple[ConnectorDocument, ...]
    next_cursor: str | None
    has_more: bool
    # Source-side deletions are explicit tombstones. IDs are scoped to this
    # connector and tenant; the service verifies ownership before deletion.
    deleted_document_ids: tuple[str, ...] = ()


class KnowledgeConnector(Protocol):
    connector_id: str

    async def list_changed(
        self,
        *,
        tenant_id: str,
        cursor: str | None,
        changed_since: datetime | None = None,
        limit: int = 100,
    ) -> ConnectorPage: ...

    async def revoke(self, *, tenant_id: str, credential_ref: str) -> None: ...
