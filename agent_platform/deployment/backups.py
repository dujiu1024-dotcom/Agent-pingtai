"""Provider-neutral backup policy, snapshot chain, off-site replication and restore drills."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Callable
from uuid import uuid4


class BackupKind(str, Enum):
    FULL = "full"
    INCREMENTAL = "incremental"


class RestoreDrillState(str, Enum):
    PLANNED = "planned"
    RUNNING = "running"
    VERIFIED = "verified"
    FAILED = "failed"


@dataclass(frozen=True)
class BackupPolicy:
    """Retention and recovery objectives; intervals are explicit durations."""

    snapshot_interval: timedelta = timedelta(hours=24)
    incremental_interval: timedelta = timedelta(hours=1)
    full_retention: timedelta = timedelta(days=30)
    incremental_retention: timedelta = timedelta(days=7)
    offsite_required: bool = True
    max_rpo: timedelta = timedelta(hours=1)
    max_rto: timedelta = timedelta(hours=4)
    restore_drill_interval: timedelta = timedelta(days=30)

    def __post_init__(self) -> None:
        for name in ("snapshot_interval", "incremental_interval", "full_retention",
                     "incremental_retention", "max_rpo", "max_rto",
                     "restore_drill_interval"):
            if getattr(self, name).total_seconds() <= 0:
                raise ValueError(f"{name} must be positive")
        if self.incremental_interval > self.snapshot_interval:
            raise ValueError("incremental interval cannot exceed full snapshot interval")
        if self.incremental_retention > self.full_retention:
            raise ValueError("incremental retention cannot exceed full retention")


@dataclass(frozen=True)
class BackupArtifact:
    backup_id: str
    tenant_id: str
    resource: str
    kind: BackupKind
    created_at: datetime
    checksum: str
    storage_uri: str
    parent_backup_id: str | None = None
    offsite_uri: str | None = None
    size_bytes: int = 0

    def __post_init__(self) -> None:
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        for name in ("backup_id", "tenant_id", "resource", "checksum", "storage_uri"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")
        if self.size_bytes < 0:
            raise ValueError("size_bytes cannot be negative")
        if self.kind == BackupKind.FULL and self.parent_backup_id is not None:
            raise ValueError("full backups cannot have a parent")
        if self.kind == BackupKind.INCREMENTAL and not self.parent_backup_id:
            raise ValueError("incremental backups require a parent")


@dataclass
class BackupCatalog:
    """Append-only catalog facade; persistence is supplied by the caller."""

    artifacts: dict[str, BackupArtifact] = field(default_factory=dict)

    def add(self, artifact: BackupArtifact) -> None:
        if artifact.backup_id in self.artifacts:
            raise ValueError("duplicate backup id")
        if artifact.parent_backup_id:
            parent = self.artifacts.get(artifact.parent_backup_id)
            if parent is None:
                raise ValueError("incremental parent does not exist")
            if (parent.tenant_id, parent.resource) != (artifact.tenant_id, artifact.resource):
                raise ValueError("backup chain cannot cross tenant or resource boundaries")
            if parent.created_at >= artifact.created_at:
                raise ValueError("incremental backup must be newer than its parent")
        self.artifacts[artifact.backup_id] = artifact

    def chain(self, backup_id: str) -> tuple[BackupArtifact, ...]:
        current = self.artifacts.get(backup_id)
        if current is None:
            raise KeyError(backup_id)
        result = [current]
        seen = {current.backup_id}
        while result[-1].parent_backup_id:
            parent_id = result[-1].parent_backup_id
            if parent_id in seen:
                raise ValueError("backup chain contains a cycle")
            parent = self.artifacts.get(parent_id)
            if parent is None:
                raise ValueError("backup chain is incomplete")
            seen.add(parent_id)
            result.append(parent)
        if result[-1].kind != BackupKind.FULL:
            raise ValueError("backup chain must terminate at a full backup")
        return tuple(reversed(result))

    def latest(self, tenant_id: str, resource: str) -> BackupArtifact | None:
        matches = [item for item in self.artifacts.values()
                   if item.tenant_id == tenant_id and item.resource == resource]
        return max(matches, key=lambda item: item.created_at) if matches else None

    def prune(self, now: datetime, policy: BackupPolicy) -> tuple[str, ...]:
        """Prune expired full roots and incrementals without leaving dangling chains."""
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        removed: set[str] = set()
        for item in self.artifacts.values():
            age = now - item.created_at
            if item.kind == BackupKind.FULL and age > policy.full_retention:
                removed.add(item.backup_id)
            elif item.kind == BackupKind.INCREMENTAL and age > policy.incremental_retention:
                removed.add(item.backup_id)
        changed = True
        while changed:
            changed = False
            for item in self.artifacts.values():
                if item.parent_backup_id in removed and item.backup_id not in removed:
                    removed.add(item.backup_id)
                    changed = True
        for backup_id in removed:
            self.artifacts.pop(backup_id, None)
        return tuple(sorted(removed))


class BackupCoordinator:
    """Coordinates provider adapters; storage and replication remain external."""

    def __init__(
        self,
        policy: BackupPolicy,
        catalog: BackupCatalog,
        create_snapshot: Callable[[str, str, BackupKind, str | None], tuple[str, str, int]],
        replicate_offsite: Callable[[str], str],
    ) -> None:
        self.policy = policy
        self.catalog = catalog
        self.create_snapshot = create_snapshot
        self.replicate_offsite = replicate_offsite

    def run(self, tenant_id: str, resource: str, kind: BackupKind,
            now: datetime | None = None) -> BackupArtifact:
        instant = now or datetime.now(timezone.utc)
        if instant.tzinfo is None:
            raise ValueError("backup time must be timezone-aware")
        latest = self.catalog.latest(tenant_id, resource)
        parent_id = latest.backup_id if kind == BackupKind.INCREMENTAL and latest else None
        if kind == BackupKind.INCREMENTAL and parent_id is None:
            raise ValueError("incremental backup requires an existing full backup")
        if kind == BackupKind.FULL:
            parent_id = None
        uri, checksum, size = self.create_snapshot(tenant_id, resource, kind, parent_id)
        artifact = BackupArtifact(
            backup_id=str(uuid4()), tenant_id=tenant_id, resource=resource, kind=kind,
            created_at=instant, checksum=checksum, storage_uri=uri,
            parent_backup_id=parent_id, size_bytes=size,
        )
        offsite_uri = self.replicate_offsite(uri) if self.policy.offsite_required else None
        if self.policy.offsite_required and not offsite_uri:
            raise RuntimeError("off-site replication did not return a durable location")
        artifact = BackupArtifact(
            backup_id=artifact.backup_id, tenant_id=artifact.tenant_id,
            resource=artifact.resource, kind=artifact.kind, created_at=artifact.created_at,
            checksum=artifact.checksum, storage_uri=artifact.storage_uri,
            parent_backup_id=artifact.parent_backup_id, offsite_uri=offsite_uri,
            size_bytes=artifact.size_bytes,
        )
        self.catalog.add(artifact)
        return artifact


@dataclass
class RestoreDrill:
    backup_id: str
    started_at: datetime
    state: RestoreDrillState = RestoreDrillState.PLANNED
    completed_at: datetime | None = None
    verification_summary: str | None = None
    failure_reason: str | None = None

    def start(self) -> None:
        if self.state != RestoreDrillState.PLANNED:
            raise ValueError("only planned restore drills can start")
        if self.started_at.tzinfo is None:
            raise ValueError("started_at must be timezone-aware")
        self.state = RestoreDrillState.RUNNING

    def finish(self, completed_at: datetime, summary: str, policy: BackupPolicy) -> None:
        if self.state != RestoreDrillState.RUNNING:
            raise ValueError("only running restore drills can finish")
        if completed_at.tzinfo is None or completed_at < self.started_at:
            raise ValueError("completed_at must be timezone-aware and not precede start")
        if not summary.strip():
            raise ValueError("verification summary is required")
        self.completed_at = completed_at
        self.verification_summary = summary
        self.state = (RestoreDrillState.VERIFIED
                      if completed_at - self.started_at <= policy.max_rto
                      else RestoreDrillState.FAILED)
        if self.state == RestoreDrillState.FAILED:
            self.failure_reason = "rto_exceeded"


def begin_restore_drill(catalog: BackupCatalog, backup_id: str,
                        now: datetime | None = None) -> RestoreDrill:
    """Validate the complete chain before a restore adapter touches a target."""
    catalog.chain(backup_id)
    drill = RestoreDrill(backup_id, now or datetime.now(timezone.utc))
    drill.start()
    return drill


def recovery_point_age(artifact: BackupArtifact, now: datetime) -> timedelta:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now - artifact.created_at


def meets_rpo(artifact: BackupArtifact, now: datetime, policy: BackupPolicy) -> bool:
    age = recovery_point_age(artifact, now)
    return timedelta(0) <= age <= policy.max_rpo
