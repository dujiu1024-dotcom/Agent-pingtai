"""Batch result aggregation and portable JSON/CSV exports."""
from __future__ import annotations

import csv
import io
import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Mapping

from agent_platform.batch.executor import BatchExecutionResult


@dataclass(frozen=True)
class BatchTaskReport:
    """Normalized, export-safe view of one batch task."""
    task_id: str
    index: int
    status: str
    passed: bool
    score: float | None
    cost: Decimal
    latency_ms: float
    output: Any
    error_type: str | None
    error_message: str | None
    started_at: datetime
    finished_at: datetime
    duration_ms: int


@dataclass(frozen=True)
class BatchReport:
    """Aggregate metrics and ordered task details for one execution."""
    batch_id: str
    tenant_id: str
    template_id: str
    total: int
    succeeded: int
    failed: int
    passed: int
    pass_rate: float
    average_score: float | None
    total_cost: Decimal
    average_latency_ms: float
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    tasks: tuple[BatchTaskReport, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible report document."""
        return _json_value({
            "batch_id": self.batch_id,
            "tenant_id": self.tenant_id,
            "template_id": self.template_id,
            "summary": {
                "total": self.total, "succeeded": self.succeeded,
                "failed": self.failed, "passed": self.passed,
                "pass_rate": self.pass_rate, "average_score": self.average_score,
                "total_cost": self.total_cost,
                "average_latency_ms": self.average_latency_ms,
                "duration_ms": self.duration_ms, "started_at": self.started_at,
                "finished_at": self.finished_at,
            },
            "tasks": [task.__dict__ for task in self.tasks],
        })

    def to_json(self, *, indent: int = 2) -> str:
        """Serialize summary and per-task details as deterministic JSON."""
        if indent < 0:
            raise ValueError("indent must be non-negative")
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=True)

    def to_csv(self) -> str:
        """Export one row per task, including metrics and diagnostics."""
        buffer = io.StringIO(newline="")
        fields = (
            "task_id", "index", "status", "passed", "score", "cost",
            "latency_ms", "output", "error_type", "error_message",
            "started_at", "finished_at", "duration_ms",
        )
        writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\\n")
        writer.writeheader()
        for task in self.tasks:
            writer.writerow({
                "task_id": task.task_id, "index": task.index, "status": task.status,
                "passed": task.passed, "score": task.score, "cost": str(task.cost),
                "latency_ms": task.latency_ms,
                "output": json.dumps(_json_value(task.output), ensure_ascii=False, sort_keys=True),
                "error_type": task.error_type or "", "error_message": task.error_message or "",
                "started_at": task.started_at.isoformat(),
                "finished_at": task.finished_at.isoformat(), "duration_ms": task.duration_ms,
            })
        return buffer.getvalue()


def _metric(output: Any, key: str) -> Any:
    return output.get(key) if isinstance(output, Mapping) else getattr(output, key, None)


def _number(value: Any, name: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        raise ValueError(f"{name} must be a finite number >= {minimum}")
    return result


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("report contains a non-finite number")
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("report contains a non-finite decimal")
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return _json_value(value.value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if hasattr(value, "__dict__"):
        return _json_value(vars(value))
    raise TypeError(f"unsupported report value: {type(value).__name__}")


def build_batch_report(result: BatchExecutionResult) -> BatchReport:
    """Aggregate results with pass-rate/score semantics aligned to model evaluation.

    A successful task passes unless its output explicitly supplies a false passed flag.
    Optional score/cost/latency_ms metrics are read from mapping keys or attributes.
    Missing scores are excluded from the average, missing costs count as zero, and
    missing latency falls back to the executor-measured task duration.
    """
    if not isinstance(result, BatchExecutionResult):
        raise TypeError("result must be a BatchExecutionResult")
    details: list[BatchTaskReport] = []
    scores: list[float] = []
    total_cost = Decimal("0")
    latency_total = 0.0
    passed = 0
    for task in result.tasks:
        output = task.output
        explicit_pass = _metric(output, "passed")
        if explicit_pass is not None and not isinstance(explicit_pass, bool):
            raise ValueError(f"task {task.task_id} passed metric must be boolean")
        task_passed = task.succeeded and explicit_pass is not False
        score_value = _metric(output, "score") if task.succeeded else None
        score = None if score_value is None else _number(score_value, "score", minimum=0)
        if score is not None:
            scores.append(score)
        cost_value = _metric(output, "cost") if task.succeeded else None
        try:
            cost = Decimal("0") if cost_value is None else Decimal(str(cost_value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"task {task.task_id} cost must be a finite number >= 0") from exc
        if not cost.is_finite() or cost < 0:
            raise ValueError(f"task {task.task_id} cost must be a finite number >= 0")
        latency_value = _metric(output, "latency_ms") if task.succeeded else None
        latency = (
            float(task.duration_ms)
            if latency_value is None
            else _number(latency_value, "latency_ms", minimum=0)
        )
        total_cost += cost
        latency_total += latency
        passed += int(task_passed)
        details.append(BatchTaskReport(
            task_id=task.task_id, index=task.index, status=task.status,
            passed=task_passed, score=score, cost=cost, latency_ms=latency,
            output=output, error_type=task.error_type, error_message=task.error_message,
            started_at=task.started_at, finished_at=task.finished_at,
            duration_ms=task.duration_ms,
        ))
    total = len(details)
    if total != result.total or result.succeeded + result.failed != result.total:
        raise ValueError("batch execution totals do not match task details")
    return BatchReport(
        batch_id=result.batch_id, tenant_id=result.tenant_id,
        template_id=result.template_id, total=total,
        succeeded=result.succeeded, failed=result.failed, passed=passed,
        pass_rate=passed / total if total else 1.0,
        average_score=sum(scores) / len(scores) if scores else None,
        total_cost=total_cost,
        average_latency_ms=latency_total / total if total else 0.0,
        started_at=result.started_at, finished_at=result.finished_at,
        duration_ms=result.duration_ms, tasks=tuple(details),
    )
