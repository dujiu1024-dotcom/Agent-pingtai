"""Shared domain contracts for the modular agent platform.

The contracts intentionally depend only on the Python standard library so
that domain logic can be tested without PostgreSQL, Redis, or an LLM provider.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Scope(str, Enum):
    RUN = "run"
    TASK = "task"
    PROJECT = "project"
    TENANT = "tenant"


class EntryKind(str, Enum):
    FACT = "fact"
    CLAIM = "claim"
    EVIDENCE = "evidence"
    CHALLENGE = "challenge"
    CONFLICT = "conflict"
    DECISION = "decision"
    ARTIFACT = "artifact"
    NOTE = "note"


class EntryStatus(str, Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    CHALLENGED = "challenged"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


@dataclass(frozen=True)
class BlackboardEntry:
    scope: Scope
    scope_id: str
    kind: EntryKind
    content: dict[str, Any]
    author_id: str
    source_refs: tuple[str, ...] = ()
    entry_id: str = field(default_factory=lambda: str(uuid4()))
    version: int = 1
    status: EntryStatus = EntryStatus.PROPOSED
    created_at: datetime = field(default_factory=utc_now)
    correlation_id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        if not self.scope_id.strip():
            raise ValueError("scope_id must not be empty")
        if not self.author_id.strip():
            raise ValueError("author_id must not be empty")
        if self.version < 1:
            raise ValueError("version must be >= 1")


@dataclass(frozen=True)
class BlackboardEvent:
    event_type: str
    scope: Scope
    scope_id: str
    entry_id: str
    entry_version: int
    actor_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid4()))
    occurred_at: datetime = field(default_factory=utc_now)
    correlation_id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        if not self.event_type.strip():
            raise ValueError("event_type must not be empty")
        if self.entry_version < 1:
            raise ValueError("entry_version must be >= 1")


@dataclass(frozen=True)
class ModelTemplateRef:
    template_id: str
    version: str = "1"

    def __post_init__(self) -> None:
        if not self.template_id.strip():
            raise ValueError("template_id must not be empty")


@dataclass(frozen=True)
class RunContext:
    tenant_id: str
    run_id: str
    agent_id: str
    model_template: ModelTemplateRef
    task_id: str | None = None
    project_id: str | None = None
    correlation_id: str = field(default_factory=lambda: str(uuid4()))
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("tenant_id", "run_id", "agent_id"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")
