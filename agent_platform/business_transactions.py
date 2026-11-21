"""Business transaction orchestration primitives for cross-system workflows.

The engine is adapter-driven: production must inject a durable transaction store,
idempotency store and connector operations. The in-memory implementation is for tests.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence


class TransactionError(RuntimeError):
    """Base error for transaction lifecycle violations."""


class IdempotencyConflict(TransactionError):
    """The same idempotency key was reused for a different request."""


class RecoveryRejected(TransactionError):
    """Manual recovery did not pass its mandatory risk check."""


class TransactionStatus(str, Enum):
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial_success"
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"
    FAILED = "failed"
    REJECTED = "rejected"
    MANUAL_REVIEW = "manual_review"


class ApprovalDecision(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    PENDING = "pending"


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    initial_delay_seconds: float = 0.0
    multiplier: float = 2.0
    max_delay_seconds: float = 30.0

    def validate(self) -> None:
        if self.max_attempts < 1 or self.max_attempts > 10:
            raise ValueError("max_attempts must be between 1 and 10")
        if self.initial_delay_seconds < 0 or self.multiplier < 1 or self.max_delay_seconds < 0:
            raise ValueError("retry delays and multiplier are invalid")

    def delay_for(self, failed_attempt: int) -> float:
        return min(self.initial_delay_seconds * self.multiplier ** max(0, failed_attempt - 1),
                   self.max_delay_seconds)


@dataclass(frozen=True)
class TransactionStep:
    step_id: str
    operation: str
    timeout_seconds: float = 30.0
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    compensation_operation: str | None = None
    requires_approval: bool = False
    allow_partial_success: bool = False

    def validate(self) -> None:
        if not self.step_id.strip() or not self.operation.strip():
            raise ValueError("step_id and operation are required")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.retry.validate()


@dataclass(frozen=True)
class StepResult:
    step_id: str
    status: str
    attempts: int
    output: Any = None
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


@dataclass(frozen=True)
class TransactionAuditEvent:
    event_type: str
    actor_id: str
    occurred_at: str
    details: Mapping[str, Any]


@dataclass
class BusinessTransaction:
    transaction_id: str
    tenant_id: str
    workflow_id: str
    workflow_version: int
    idempotency_key: str
    request_fingerprint: str
    status: TransactionStatus = TransactionStatus.RUNNING
    step_results: dict[str, StepResult] = field(default_factory=dict)
    compensation_results: dict[str, StepResult] = field(default_factory=dict)
    business_receipt: Mapping[str, Any] | None = None
    failure_reason: str | None = None
    created_at: str = field(default_factory=lambda: _now())
    updated_at: str = field(default_factory=lambda: _now())
    audit: list[TransactionAuditEvent] = field(default_factory=list)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class TransactionRepository(Protocol):
    async def reserve(self, transaction: BusinessTransaction) -> tuple[BusinessTransaction, bool]: ...
    async def get(self, tenant_id: str, transaction_id: str) -> BusinessTransaction | None: ...
    async def save(self, transaction: BusinessTransaction) -> None: ...


class InMemoryTransactionRepository:
    """Atomic process-local adapter for tests; production requires durable unique constraints."""

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str], BusinessTransaction] = {}
        self._by_id: dict[tuple[str, str], BusinessTransaction] = {}
        self._lock = asyncio.Lock()

    async def reserve(self, transaction: BusinessTransaction) -> tuple[BusinessTransaction, bool]:
        key = (transaction.tenant_id, transaction.idempotency_key)
        async with self._lock:
            existing = self._by_key.get(key)
            if existing:
                if existing.request_fingerprint != transaction.request_fingerprint:
                    raise IdempotencyConflict("idempotency key already belongs to a different request")
                return existing, False
            self._by_key[key] = transaction
            self._by_id[(transaction.tenant_id, transaction.transaction_id)] = transaction
            return transaction, True

    async def get(self, tenant_id: str, transaction_id: str) -> BusinessTransaction | None:
        async with self._lock:
            return self._by_id.get((tenant_id, transaction_id))

    async def save(self, transaction: BusinessTransaction) -> None:
        async with self._lock:
            key = (transaction.tenant_id, transaction.idempotency_key)
            current = self._by_key.get(key)
            if current is not transaction:
                raise TransactionError("transaction reservation is missing or tenant boundary changed")
            transaction.updated_at = _now()


Operation = Callable[[Mapping[str, Any]], Awaitable[Any]]
ApprovalHandler = Callable[[TransactionStep, Mapping[str, Any]], Awaitable[ApprovalDecision]]
RiskCheck = Callable[[BusinessTransaction, str], Awaitable[bool]]


class BusinessTransactionEngine:
    """Executes ordered steps with idempotency, bounded retries, timeouts and Saga."""

    def __init__(
        self,
        *,
        repository: TransactionRepository,
        operations: Mapping[str, Operation],
        compensations: Mapping[str, Operation] | None = None,
        approval_handler: ApprovalHandler | None = None,
    ) -> None:
        self.repository = repository
        self.operations = dict(operations)
        self.compensations = dict(compensations or {})
        self.approval_handler = approval_handler

    async def execute(
        self,
        *,
        transaction_id: str,
        tenant_id: str,
        workflow_id: str,
        workflow_version: int,
        idempotency_key: str,
        steps: Sequence[TransactionStep],
        payload: Mapping[str, Any],
        actor_id: str,
        saga: bool = True,
        business_receipt: Mapping[str, Any] | None = None,
    ) -> BusinessTransaction:
        if not all(v.strip() for v in (transaction_id, tenant_id, workflow_id, idempotency_key, actor_id)):
            raise ValueError("transaction, tenant, workflow, idempotency key and actor are required")
        if workflow_version < 1 or not steps:
            raise ValueError("positive workflow version and at least one step are required")
        for step in steps:
            step.validate()
            if step.operation not in self.operations:
                raise ValueError(f"operation adapter is not registered: {step.operation}")
            if step.compensation_operation and step.compensation_operation not in self.compensations:
                raise ValueError(f"compensation adapter is not registered: {step.compensation_operation}")
        fingerprint = _fingerprint({
            "workflow_id": workflow_id, "workflow_version": workflow_version,
            "steps": [asdict(step) for step in steps], "payload": payload,
        })
        transaction = BusinessTransaction(
            transaction_id=transaction_id, tenant_id=tenant_id, workflow_id=workflow_id,
            workflow_version=workflow_version, idempotency_key=idempotency_key,
            request_fingerprint=fingerprint, business_receipt=business_receipt,
        )
        transaction, is_new = await self.repository.reserve(transaction)
        if not is_new:
            return transaction
        self._audit(transaction, "transaction.started", actor_id, {"workflow_version": workflow_version})
        await self.repository.save(transaction)

        for step in steps:
            if step.requires_approval:
                if self.approval_handler is None:
                    transaction.status = TransactionStatus.WAITING_APPROVAL
                    transaction.failure_reason = "approval handler is not configured"
                    self._audit(transaction, "approval.waiting", actor_id, {"step_id": step.step_id})
                    await self.repository.save(transaction)
                    return transaction
                decision = await self.approval_handler(step, payload)
                self._audit(transaction, "approval.decision", actor_id,
                            {"step_id": step.step_id, "decision": decision.value})
                if decision == ApprovalDecision.PENDING:
                    transaction.status = TransactionStatus.WAITING_APPROVAL
                    await self.repository.save(transaction)
                    return transaction
                if decision == ApprovalDecision.REJECTED:
                    transaction.status = TransactionStatus.REJECTED
                    transaction.failure_reason = f"approval rejected at {step.step_id}"
                    if saga and transaction.step_results:
                        await self._compensate(transaction, steps, actor_id)
                        if transaction.status == TransactionStatus.COMPENSATED:
                            transaction.status = TransactionStatus.REJECTED
                            await self.repository.save(transaction)
                    else:
                        await self.repository.save(transaction)
                    return transaction

            step_payload = dict(payload)
            step_payload["_idempotency_key"] = f"{idempotency_key}:{step.step_id}"
            result = await self._invoke(step, self.operations[step.operation], step_payload)
            transaction.step_results[step.step_id] = result
            await self.repository.save(transaction)
            if result.status != "succeeded":
                transaction.failure_reason = result.error
                if step.allow_partial_success:
                    transaction.status = TransactionStatus.PARTIAL
                    self._audit(transaction, "transaction.partial", actor_id, {"failed_step": step.step_id})
                    await self.repository.save(transaction)
                    return transaction
                if saga:
                    await self._compensate(transaction, steps, actor_id)
                else:
                    transaction.status = TransactionStatus.FAILED
                    self._audit(transaction, "transaction.failed", actor_id, {"failed_step": step.step_id})
                    await self.repository.save(transaction)
                return transaction

        transaction.status = TransactionStatus.SUCCEEDED
        self._audit(transaction, "transaction.succeeded", actor_id,
                    {"completed_steps": list(transaction.step_results)})
        await self.repository.save(transaction)
        return transaction

    async def _invoke(self, step: TransactionStep, operation: Operation,
                      payload: Mapping[str, Any]) -> StepResult:
        started = _now()
        last_error = "operation failed"
        for attempt in range(1, step.retry.max_attempts + 1):
            try:
                output = await asyncio.wait_for(operation(payload), timeout=step.timeout_seconds)
                return StepResult(step.step_id, "succeeded", attempt, output=output,
                                  started_at=started, finished_at=_now())
            except (Exception, asyncio.TimeoutError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"[:500]
                if attempt < step.retry.max_attempts:
                    delay = step.retry.delay_for(attempt)
                    if delay:
                        await asyncio.sleep(delay)
        return StepResult(step.step_id, "failed", step.retry.max_attempts, error=last_error,
                          started_at=started, finished_at=_now())

    async def _compensate(self, transaction: BusinessTransaction,
                          steps: Sequence[TransactionStep], actor_id: str) -> None:
        transaction.status = TransactionStatus.COMPENSATING
        self._audit(transaction, "saga.compensation_started", actor_id, {})
        await self.repository.save(transaction)
        by_id = {step.step_id: step for step in steps}
        for step_id in reversed(list(transaction.step_results)):
            step = by_id[step_id]
            if transaction.step_results[step_id].status != "succeeded" or not step.compensation_operation:
                continue
            result = await self._invoke(
                TransactionStep(step_id=step_id, operation=step.compensation_operation,
                                timeout_seconds=step.timeout_seconds, retry=step.retry),
                self.compensations[step.compensation_operation],
                {"transaction_id": transaction.transaction_id,
                 "tenant_id": transaction.tenant_id,
                 "step_output": transaction.step_results[step_id].output,
                 "_idempotency_key": f"{transaction.idempotency_key}:compensate:{step_id}"},
            )
            transaction.compensation_results[step_id] = result
            await self.repository.save(transaction)
            if result.status != "succeeded":
                transaction.status = TransactionStatus.MANUAL_REVIEW
                transaction.failure_reason = f"compensation failed at {step_id}: {result.error}"
                self._audit(transaction, "saga.compensation_failed", actor_id, {"step_id": step_id})
                await self.repository.save(transaction)
                return
        transaction.status = TransactionStatus.COMPENSATED
        self._audit(transaction, "saga.compensated", actor_id, {})
        await self.repository.save(transaction)

    async def recover_step(
        self, *, tenant_id: str, transaction_id: str, step: TransactionStep,
        actor_id: str, risk_check: RiskCheck, payload: Mapping[str, Any],
    ) -> BusinessTransaction:
        transaction = await self.repository.get(tenant_id, transaction_id)
        if transaction is None:
            raise KeyError("transaction not found")
        if transaction.status not in {TransactionStatus.PARTIAL, TransactionStatus.FAILED,
                                      TransactionStatus.MANUAL_REVIEW, TransactionStatus.WAITING_APPROVAL}:
            raise TransactionError("transaction status does not permit manual recovery")
        if not await risk_check(transaction, step.step_id):
            self._audit(transaction, "recovery.rejected", actor_id, {"step_id": step.step_id})
            await self.repository.save(transaction)
            raise RecoveryRejected("replay risk check rejected manual recovery")
        previous = transaction.step_results.get(step.step_id)
        if previous and previous.status == "succeeded":
            raise TransactionError("refusing to replay a step already recorded as successful")
        step.validate()
        operation = self.operations.get(step.operation)
        if operation is None:
            raise ValueError("operation adapter is not registered")
        self._audit(transaction, "recovery.started", actor_id, {"step_id": step.step_id})
        recovery_payload = dict(payload)
        recovery_payload["_idempotency_key"] = f"{transaction.idempotency_key}:{step.step_id}"
        result = await self._invoke(step, operation, recovery_payload)
        transaction.step_results[step.step_id] = result
        transaction.status = TransactionStatus.PARTIAL if result.status == "succeeded" else TransactionStatus.FAILED
        transaction.failure_reason = result.error
        self._audit(transaction, "recovery.completed", actor_id,
                    {"step_id": step.step_id, "result": result.status})
        await self.repository.save(transaction)
        return transaction

    @staticmethod
    def _audit(transaction: BusinessTransaction, event_type: str, actor_id: str,
               details: Mapping[str, Any]) -> None:
        transaction.audit.append(TransactionAuditEvent(event_type, actor_id, _now(), dict(details)))
