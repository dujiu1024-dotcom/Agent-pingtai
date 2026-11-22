"""Hybrid control-plane degradation, policy synchronization and Run node handoff.

The module composes trusted deployment adapters. Signature verification,
snapshot persistence, node fencing and worker lifecycle remain explicit
infrastructure ports; unverified cache and unfenced nodes are never authoritative.
"""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import inspect
import json
import os
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Protocol, cast


class ControlPlaneMode(str, Enum):
    ONLINE = "online"
    DEGRADED = "degraded"
    RECONCILING = "reconciling"


@dataclass(frozen=True)
class SignedPolicySnapshot:
    tenant_id: str
    revision: int
    policy_version: str
    issued_at: int
    expires_at: int
    revocation_epoch: int
    payload: Mapping[str, Any]
    signature: str

    def __post_init__(self) -> None:
        if not self.tenant_id.strip() or not self.policy_version.strip():
            raise ValueError("tenant_id and policy_version are required")
        if self.revision < 1 or self.revocation_epoch < 0:
            raise ValueError("snapshot revision and revocation epoch are invalid")
        if self.issued_at < 0 or self.expires_at <= self.issued_at:
            raise ValueError("snapshot validity interval is invalid")
        if not self.signature.strip():
            raise ValueError("signed policy snapshot signature is required")


class PolicySnapshotStore(Protocol):
    async def get(self, tenant_id: str) -> SignedPolicySnapshot | None: ...

    async def put_if_newer(
        self, snapshot: SignedPolicySnapshot, *, expected_revision: int | None,
    ) -> bool:
        """Atomically persist only if the current revision still matches."""


class LocalFilePolicySnapshotStore:
    """Cross-process CAS store using advisory locks and atomic durable replacement.

    Each tenant has a separately hashed filename. Writers serialize on a stable
    lock file, verify the expected revision while holding the lock, fsync a
    private temporary file, atomically replace the snapshot, then fsync the
    directory entry. The async API delegates blocking filesystem work to a
    worker thread so it does not block the event loop.
    """

    def __init__(self, directory: str | os.PathLike[str]) -> None:
        self._directory = Path(directory)

    def _paths(self, tenant_id: str) -> tuple[Path, Path]:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id is required")
        key = hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()
        return self._directory / f"{key}.json", self._directory / f"{key}.lock"

    @staticmethod
    def _encode(snapshot: SignedPolicySnapshot) -> bytes:
        document = {
            "tenant_id": snapshot.tenant_id,
            "revision": snapshot.revision,
            "policy_version": snapshot.policy_version,
            "issued_at": snapshot.issued_at,
            "expires_at": snapshot.expires_at,
            "revocation_epoch": snapshot.revocation_epoch,
            "payload": dict(snapshot.payload),
            "signature": snapshot.signature,
        }
        try:
            return (json.dumps(
                document, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                allow_nan=False,
            ) + "\n").encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("policy snapshot payload must be finite JSON data") from exc

    @staticmethod
    def _decode(raw: bytes, tenant_id: str) -> SignedPolicySnapshot:
        try:
            document = json.loads(raw)
            if not isinstance(document, dict) or document.get("tenant_id") != tenant_id:
                raise ValueError("policy snapshot tenant scope mismatch")
            payload = document["payload"]
            if not isinstance(payload, dict):
                raise ValueError("policy snapshot payload must be an object")
            return SignedPolicySnapshot(
                tenant_id=document["tenant_id"],
                revision=document["revision"],
                policy_version=document["policy_version"],
                issued_at=document["issued_at"],
                expires_at=document["expires_at"],
                revocation_epoch=document["revocation_epoch"],
                payload=payload,
                signature=document["signature"],
            )
        except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("stored policy snapshot is corrupt; refusing to load") from exc
        except ValueError as exc:
            raise RuntimeError("stored policy snapshot is invalid; refusing to load") from exc

    def _locked(self, tenant_id: str):
        snapshot_path, lock_path = self._paths(tenant_id)
        self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(lock_path, flags, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return snapshot_path, descriptor

    @staticmethod
    def _unlock(descriptor: int) -> None:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def _get_sync(self, tenant_id: str) -> SignedPolicySnapshot | None:
        snapshot_path, lock_fd = self._locked(tenant_id)
        try:
            try:
                raw = snapshot_path.read_bytes()
            except FileNotFoundError:
                return None
            return self._decode(raw, tenant_id)
        finally:
            self._unlock(lock_fd)

    def _put_sync(
        self, snapshot: SignedPolicySnapshot, expected_revision: int | None,
    ) -> bool:
        snapshot_path, lock_fd = self._locked(snapshot.tenant_id)
        temporary_path: str | None = None
        try:
            current: SignedPolicySnapshot | None
            try:
                current = self._decode(snapshot_path.read_bytes(), snapshot.tenant_id)
            except FileNotFoundError:
                current = None
            actual_revision = current.revision if current is not None else None
            if actual_revision != expected_revision:
                return False
            if current is not None:
                if snapshot.revision <= current.revision:
                    return False
                if snapshot.revocation_epoch < current.revocation_epoch:
                    return False
            encoded = self._encode(snapshot)
            fd, temporary_path = tempfile.mkstemp(
                prefix=f".{snapshot_path.name}.", suffix=".tmp", dir=self._directory,
            )
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary_path, snapshot_path)
                temporary_path = None
                directory_fd = os.open(self._directory, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except Exception:
                try:
                    os.close(fd)
                except OSError:
                    pass
                raise
            return True
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass
            self._unlock(lock_fd)

    async def get(self, tenant_id: str) -> SignedPolicySnapshot | None:
        return await asyncio.to_thread(self._get_sync, tenant_id)

    async def put_if_newer(
        self, snapshot: SignedPolicySnapshot, *, expected_revision: int | None,
    ) -> bool:
        if expected_revision is not None and expected_revision < 1:
            raise ValueError("expected_revision must be positive or None")
        return await asyncio.to_thread(self._put_sync, snapshot, expected_revision)


@dataclass(frozen=True)
class PolicySyncResult:
    tenant_id: str
    revision: int
    policy_version: str
    revocation_epoch: int
    changed: bool


class PolicySnapshotRejected(ValueError):
    """A policy snapshot failed trust, freshness or monotonicity checks."""


class OfflinePolicySynchronizer:
    """Verify then atomically advance a tenant's signed policy snapshot."""

    def __init__(
        self,
        *,
        store: PolicySnapshotStore,
        verify_signature: Callable[[SignedPolicySnapshot], bool],
        clock: Callable[[], int],
        audit: Callable[[Mapping[str, Any]], Awaitable[None] | None],
    ) -> None:
        self._store = store
        self._verify_signature = verify_signature
        self._clock = clock
        self._audit = audit

    async def _audit_event(self, event: Mapping[str, Any]) -> None:
        result = self._audit(event)
        if inspect.isawaitable(result):
            await result

    async def synchronize(
        self, snapshot: SignedPolicySnapshot, *, expected_tenant_id: str,
    ) -> PolicySyncResult:
        if snapshot.tenant_id != expected_tenant_id:
            raise PolicySnapshotRejected("policy snapshot tenant mismatch")
        if not self._verify_signature(snapshot):
            raise PolicySnapshotRejected("policy snapshot signature is invalid")
        now = self._clock()
        if now < snapshot.issued_at or now >= snapshot.expires_at:
            raise PolicySnapshotRejected("policy snapshot is not currently valid")

        # A bounded retry handles concurrent synchronizers without accepting
        # a rollback or silently overwriting a newer revocation watermark.
        for _ in range(3):
            current = await self._store.get(snapshot.tenant_id)
            expected = current.revision if current is not None else None
            if current is not None:
                if snapshot.revision < current.revision:
                    raise PolicySnapshotRejected("policy snapshot revision rollback")
                if snapshot.revocation_epoch < current.revocation_epoch:
                    raise PolicySnapshotRejected("revocation epoch rollback")
                if snapshot.revision == current.revision:
                    if snapshot != current:
                        raise PolicySnapshotRejected(
                            "same policy revision contains different snapshot data"
                        )
                    await self._audit_event({
                        "event": "policy_sync_confirmed", "tenant_id": snapshot.tenant_id,
                        "revision": snapshot.revision, "changed": False,
                    })
                    return PolicySyncResult(
                        snapshot.tenant_id, snapshot.revision, snapshot.policy_version,
                        snapshot.revocation_epoch, False,
                    )
            if await self._store.put_if_newer(snapshot, expected_revision=expected):
                await self._audit_event({
                    "event": "policy_snapshot_advanced", "tenant_id": snapshot.tenant_id,
                    "revision": snapshot.revision, "policy_version": snapshot.policy_version,
                    "revocation_epoch": snapshot.revocation_epoch, "changed": True,
                })
                return PolicySyncResult(
                    snapshot.tenant_id, snapshot.revision, snapshot.policy_version,
                    snapshot.revocation_epoch, True,
                )
        raise RuntimeError("policy snapshot changed concurrently; retry synchronization")


AsyncAction = Callable[[], Awaitable[None] | None]


async def _invoke(action: AsyncAction) -> None:
    result = action()
    if inspect.isawaitable(result):
        await result


class ControlPlaneReconciler:
    """Reconnect in security order: revocations, policy, license, replay, resume."""

    def __init__(
        self,
        *,
        synchronize_policy: AsyncAction,
        apply_revocations: AsyncAction,
        reconcile_license: AsyncAction,
        replay_outbox: AsyncAction,
        resume_paused_runs: AsyncAction,
        audit: Callable[[Mapping[str, Any]], Awaitable[None] | None],
    ) -> None:
        self.mode = ControlPlaneMode.ONLINE
        self._synchronize_policy = synchronize_policy
        self._apply_revocations = apply_revocations
        self._reconcile_license = reconcile_license
        self._replay_outbox = replay_outbox
        self._resume_paused_runs = resume_paused_runs
        self._audit = audit

    async def control_plane_unavailable(self, *, reason: str) -> ControlPlaneMode:
        if not reason.strip():
            raise ValueError("degradation reason is required")
        self.mode = ControlPlaneMode.DEGRADED
        await _invoke(lambda: self._audit({
            "event": "control_plane_degraded", "reason": reason[:200],
        }))
        return self.mode

    async def reconcile(self) -> ControlPlaneMode:
        self.mode = ControlPlaneMode.RECONCILING
        try:
            # Audit is part of the recovery transaction boundary: a missing
            # transition record must never leave the reconciler looking healthy.
            await _invoke(lambda: self._audit({"event": "control_plane_reconciling"}))
            await _invoke(self._synchronize_policy)
            await _invoke(self._apply_revocations)
            await _invoke(self._reconcile_license)
            await _invoke(self._replay_outbox)
            await _invoke(self._resume_paused_runs)
            await _invoke(lambda: self._audit({"event": "control_plane_online"}))
        except Exception as exc:
            self.mode = ControlPlaneMode.DEGRADED
            # Preserve the original failure. Audit may itself be unavailable,
            # which is precisely why the in-memory state remains fail-closed.
            try:
                await _invoke(lambda exc=exc: self._audit({
                    "event": "control_plane_reconcile_failed",
                    "error_type": type(exc).__name__,
                }))
            except Exception:
                pass
            raise
        self.mode = ControlPlaneMode.ONLINE
        return self.mode


NodeAction = Callable[[str], Awaitable[None] | None]
FenceConfirmation = Callable[[str], Awaitable[bool] | bool]


class RunNodeHandoff:
    """Fence the former owner before starting its peer; workers share durable leases."""

    def __init__(
        self,
        *,
        stop_worker: NodeAction,
        fence_node: NodeAction,
        confirm_fenced: FenceConfirmation,
        start_worker: NodeAction,
        audit: Callable[[Mapping[str, Any]], Awaitable[None] | None],
    ) -> None:
        self._stop_worker = stop_worker
        self._fence_node = fence_node
        self._confirm_fenced = confirm_fenced
        self._start_worker = start_worker
        self._audit = audit
        self.active_node: str | None = None

    async def _handoff(self, *, former: str, target: str, operation: str) -> str:
        if not former.strip() or not target.strip() or former == target:
            raise ValueError("handoff requires two distinct node identifiers")
        if self.active_node is not None and self.active_node != former:
            raise RuntimeError("handoff source is not the active Run node")
        await _invoke(lambda: self._stop_worker(former))
        self.active_node = None
        await _invoke(lambda: self._fence_node(former))
        confirmed = self._confirm_fenced(former)
        if inspect.isawaitable(confirmed):
            confirmed = await cast(Awaitable[bool], confirmed)
        if not confirmed:
            raise RuntimeError("former Run node is not fenced; takeover refused")
        await _invoke(lambda: self._start_worker(target))
        self.active_node = target
        await _invoke(lambda: self._audit({
            "event": "run_node_handoff_completed", "operation": operation,
            "former_node": former, "active_node": target,
        }))
        return target

    async def failover(self, *, primary: str, recovery: str) -> str:
        return await self._handoff(
            former=primary, target=recovery, operation="failover",
        )

    async def failback(self, *, recovery: str, primary: str) -> str:
        return await self._handoff(
            former=recovery, target=primary, operation="failback",
        )
