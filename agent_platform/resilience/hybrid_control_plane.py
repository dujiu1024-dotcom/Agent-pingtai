"""Hybrid control-plane degradation, policy synchronization and Run node handoff.

The module composes trusted deployment adapters. Signature verification, durable
snapshot storage, node fencing and worker lifecycle remain infrastructure ports;
this code never treats an unverified cache or an unfenced node as authoritative.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Mapping, Protocol


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
        await _invoke(lambda: self._audit({"event": "control_plane_reconciling"}))
        try:
            await _invoke(self._synchronize_policy)
            await _invoke(self._apply_revocations)
            await _invoke(self._reconcile_license)
            await _invoke(self._replay_outbox)
            await _invoke(self._resume_paused_runs)
        except Exception as exc:
            self.mode = ControlPlaneMode.DEGRADED
            await _invoke(lambda: self._audit({
                "event": "control_plane_reconcile_failed",
                "error_type": type(exc).__name__,
            }))
            raise
        self.mode = ControlPlaneMode.ONLINE
        await _invoke(lambda: self._audit({"event": "control_plane_online"}))
        return self.mode


class RunNodeHandoff:
    """Fence the former owner before starting its peer; workers share durable leases."""

    def __init__(
        self,
        *,
        stop_worker: Callable[[str], Awaitable[None]],
        fence_node: Callable[[str], Awaitable[None]],
        confirm_fenced: Callable[[str], Awaitable[bool]],
        start_worker: Callable[[str], Awaitable[None]],
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
        await self._stop_worker(former)
        await self._fence_node(former)
        if not await self._confirm_fenced(former):
            raise RuntimeError("former Run node is not fenced; takeover refused")
        await self._start_worker(target)
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
