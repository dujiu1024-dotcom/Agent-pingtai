"""Hybrid Blackboard repository contract.

Persistence implementations must use optimistic version checks and append-only
decision history. Agent-facing code receives this port, never a raw DB handle.
"""

from __future__ import annotations

from typing import Protocol, Sequence

from agent_platform.contracts import BlackboardEntry, BlackboardEvent, Scope


class VersionConflict(RuntimeError):
    """Raised when an entry changed after the caller read its version."""


class BlackboardRepository(Protocol):
    async def propose(self, entry: BlackboardEntry) -> BlackboardEntry:
        """Append a new proposed entry; never overwrite an existing entry."""
        ...

    async def get(self, entry_id: str) -> BlackboardEntry | None:
        ...

    async def list_entries(
        self,
        *,
        scope: Scope,
        scope_id: str,
        kinds: Sequence[str] = (),
        limit: int = 100,
    ) -> list[BlackboardEntry]:
        ...

    async def list_all(
        self,
        *,
        scope: Scope | None = None,
        scope_id: str | None = None,
        kinds: Sequence[str] = (),
        limit: int = 100,
    ) -> list[BlackboardEntry]:
        """List entries at the tenant level, optionally filtered by scope/scope_id."""
        ...

    async def list_versions(self, entry_id: str) -> list[BlackboardEntry]:
        """Return the immutable version chain for one tenant-scoped entry."""
        ...

    async def revise(
        self,
        entry_id: str,
        *,
        expected_version: int,
        replacement: BlackboardEntry,
    ) -> BlackboardEntry:
        """Append a new version only if expected_version is still current."""
        ...

    async def append_event(self, event: BlackboardEvent) -> None:
        """Persist event in the same transaction as its outbox record."""
        ...

    async def record_challenge(
        self, entry_id: str, challenger_id: str, reason: str, evidence_refs: Sequence[str]
    ) -> BlackboardEntry:
        ...

    async def record_decision(
        self, conflict_id: str, decider_id: str, outcome: str, rationale: str,
        evidence_refs: Sequence[str]
    ) -> BlackboardEntry:
        """Append an immutable decision; corrections require a new decision."""
        ...
