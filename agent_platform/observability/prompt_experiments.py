"""Tenant-scoped, reproducible A/B experiments for prompt variants.

Execution and LLM-as-judge providers are injected by the host. The default
scorer reuses the deterministic evaluation primitives from observability.evaluation.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import math
import time
from dataclasses import asdict, dataclass, is_dataclass
from types import MappingProxyType
from typing import Any, Mapping, Protocol
from uuid import uuid4

from agent_platform.observability.evaluation import EvalCase, evaluate_case
from agent_platform.observability.evaluation_governance import (
    MetricDelta,
    RegressionComparison,
    compare_metric_reports,
)
from agent_platform.observability.prompts import PromptVersion, tenant_prompt_key


def _sanitize_nan_inf(obj: Any) -> Any:
    """Recursively replace NaN/Inf with None for json.dumps safety."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_nan_inf(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_nan_inf(v) for v in obj]
    return obj


def _sanitize_json_value(obj: Any) -> Any:
    """json.dumps default fallback — stringify non-serializable objects."""
    if hasattr(obj, "__float__"):
        val = float(obj)
        if math.isnan(val) or math.isinf(val):
            return None
        return val
    if hasattr(obj, "__dict__"):
        return str(obj)
    return str(obj)


@dataclass(frozen=True)
class PromptExperimentCase:
    case_id: str
    variables: Mapping[str, str]
    expected_terms: tuple[str, ...] = ()
    forbidden_terms: tuple[str, ...] = ()
    rubric: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id.strip():
            raise ValueError("case_id is required")
        if not isinstance(self.variables, Mapping) or any(
            not isinstance(key, str) or not key.strip() or not isinstance(value, str)
            for key, value in self.variables.items()
        ):
            raise ValueError("case variables must map non-empty names to strings")
        if len(self.variables) > 64 or sum(map(len, self.variables.values())) > 32_000:
            raise ValueError("case variables exceed the experiment input budget")
        if any(not isinstance(term, str) or not term.strip() for term in (
            *self.expected_terms, *self.forbidden_terms,
        )):
            raise ValueError("rubric terms must be non-empty strings")
        if not isinstance(self.rubric, str) or len(self.rubric) > 4_000:
            raise ValueError("rubric must be a string of at most 4000 characters")
        object.__setattr__(self, "variables", MappingProxyType(dict(self.variables)))


@dataclass(frozen=True)
class PromptExperimentDataset:
    dataset_id: str
    version: int
    cases: tuple[PromptExperimentCase, ...]
    created_by: str

    def __post_init__(self) -> None:
        if not isinstance(self.dataset_id, str) or not self.dataset_id.strip():
            raise ValueError("dataset_id is required")
        if type(self.version) is not int or self.version < 1:
            raise ValueError("dataset version must be a positive integer")
        if not isinstance(self.created_by, str) or not self.created_by.strip():
            raise ValueError("created_by is required")
        if not isinstance(self.cases, tuple) or not 1 <= len(self.cases) <= 500:
            raise ValueError("dataset must contain between 1 and 500 cases")
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("dataset case IDs must be unique")


@dataclass(frozen=True)
class PromptExecution:
    output_text: str
    latency_ms: float = 0.0
    cost: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.output_text, str) or len(self.output_text) > 100_000:
            raise ValueError("prompt output must be a string of at most 100000 characters")
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0
            for value in (self.latency_ms, self.cost)
        ):
            raise ValueError("latency and cost must be finite non-negative numbers")


@dataclass(frozen=True)
class PromptCaseScore:
    case_id: str
    variant: str
    version: int
    passed: bool
    rule_score: float
    judge_score: float | None
    final_score: float
    latency_ms: float
    cost: float
    error: str | None = None


@dataclass(frozen=True)
class PromptVariantMetrics:
    variant: str
    version: int
    case_count: int
    pass_rate: float
    average_score: float
    average_latency_ms: float
    total_cost: float


@dataclass(frozen=True)
class PromptExperimentReport:
    experiment_id: str
    tenant_id: str
    prompt_id: str
    dataset_id: str
    dataset_version: int
    baseline_variant: str
    matrix: tuple[PromptCaseScore, ...]
    metrics: tuple[PromptVariantMetrics, ...]
    comparison: Any
    created_at_epoch_ms: int


class PromptVariantResolver(Protocol):
    def list_variants(self, prompt_id: str) -> Any: ...
    def resolve(self, prompt_id: str, *, version: int) -> PromptVersion: ...


class PromptVariantExecutor(Protocol):
    async def execute(
        self, *, tenant_id: str, prompt_id: str, variant: str, version: int,
        rendered_prompt: str, variables: Mapping[str, str],
    ) -> PromptExecution: ...


class PromptJudge(Protocol):
    async def score(
        self, *, rubric: str, variables: Mapping[str, str], output_text: str,
    ) -> float: ...


class PromptExperimentRepository(Protocol):
    async def save(self, report: PromptExperimentReport) -> None: ...
    async def get(self, tenant_id: str, experiment_id: str) -> PromptExperimentReport | None: ...


class InMemoryPromptExperimentRepository:
    """Immutable tenant-scoped report store for local/test deployments."""

    def __init__(self) -> None:
        self._reports: dict[tuple[str, str], PromptExperimentReport] = {}
        self._lock = asyncio.Lock()

    async def save(self, report: PromptExperimentReport) -> None:
        key = (report.tenant_id, report.experiment_id)
        async with self._lock:
            if key in self._reports:
                raise ValueError("experiment report already exists")
            self._reports[key] = report

    async def get(self, tenant_id: str, experiment_id: str) -> PromptExperimentReport | None:
        async with self._lock:
            return self._reports.get((tenant_id, experiment_id))


class PromptABTestService:
    def __init__(
        self, *, registry: PromptVariantResolver, executor: PromptVariantExecutor,
        repository: PromptExperimentRepository, judge: PromptJudge | None = None,
        concurrency: int = 4, timeout_seconds: float = 60.0,
        judge_weight: float = 0.5,
    ) -> None:
        if type(concurrency) is not int or not 1 <= concurrency <= 32:
            raise ValueError("concurrency must be between 1 and 32")
        if (
            isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 300
        ):
            raise ValueError("timeout_seconds must be in (0, 300]")
        if (
            isinstance(judge_weight, bool) or not isinstance(judge_weight, (int, float))
            or not math.isfinite(judge_weight) or not 0 <= judge_weight <= 1
        ):
            raise ValueError("judge_weight must be between 0 and 1")
        self.registry = registry
        self.executor = executor
        self.repository = repository
        self.judge = judge
        self.concurrency = concurrency
        self.timeout_seconds = float(timeout_seconds)
        self.judge_weight = float(judge_weight)

    async def run(
        self, *, tenant_id: str, prompt_id: str,
        dataset: PromptExperimentDataset, variants: tuple[str, ...],
        baseline_variant: str,
    ) -> PromptExperimentReport:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id is required")
        if not isinstance(prompt_id, str) or not prompt_id.strip():
            raise ValueError("prompt_id is required")
        if not isinstance(dataset, PromptExperimentDataset):
            raise ValueError("dataset is required")
        if not isinstance(variants, tuple) or not 2 <= len(variants) <= 8:
            raise ValueError("experiments require between 2 and 8 variants")
        if len(set(variants)) != len(variants) or any(
            not isinstance(name, str) or not name.strip() for name in variants
        ):
            raise ValueError("variant names must be unique non-empty strings")
        if baseline_variant not in variants:
            raise ValueError("baseline_variant must be one of the experiment variants")

        scoped_prompt_id = tenant_prompt_key(tenant_id, prompt_id)
        listed = self.registry.list_variants(scoped_prompt_id)
        if inspect.isawaitable(listed):
            listed = await listed
        available = {item.name: item for item in listed if item.status == "active"}
        if any(name not in available for name in variants):
            raise KeyError("one or more active prompt variants do not exist in this tenant")
        snapshots = {}
        for name in variants:
            snapshot = self.registry.resolve(
                scoped_prompt_id, version=available[name].current_version,
            )
            snapshots[name] = await snapshot if inspect.isawaitable(snapshot) else snapshot
        if any(snapshot.prompt_id != scoped_prompt_id for snapshot in snapshots.values()):
            raise PermissionError("prompt resolver returned a cross-tenant version")

        semaphore = asyncio.Semaphore(self.concurrency)

        async def execute_one(case: PromptExperimentCase, variant: str) -> PromptCaseScore:
            prompt = snapshots[variant]
            try:
                expected = set(prompt.variables)
                supplied = set(case.variables)
                if expected != supplied:
                    raise ValueError("experiment variables do not match the prompt version")
                rendered = prompt.template.format_map(dict(case.variables))
                async with semaphore:
                    started = time.perf_counter()
                    execution = await asyncio.wait_for(
                        self.executor.execute(
                            tenant_id=tenant_id, prompt_id=prompt_id, variant=variant,
                            version=prompt.version, rendered_prompt=rendered,
                            variables=dict(case.variables),
                        ),
                        timeout=self.timeout_seconds,
                    )
                    measured_latency = (time.perf_counter() - started) * 1000
                if not isinstance(execution, PromptExecution):
                    raise TypeError("executor must return PromptExecution")
                rule = evaluate_case(
                    EvalCase(
                        case_id=case.case_id,
                        expected_terms=case.expected_terms,
                        forbidden_terms=case.forbidden_terms,
                    ),
                    output_text=execution.output_text,
                )
                judge_score = None
                if self.judge is not None and case.rubric:
                    async with semaphore:
                        judge_score = await asyncio.wait_for(
                            self.judge.score(
                                rubric=case.rubric, variables=dict(case.variables),
                                output_text=execution.output_text,
                            ),
                            timeout=self.timeout_seconds,
                        )
                    if (
                        isinstance(judge_score, bool)
                        or not isinstance(judge_score, (int, float))
                        or not math.isfinite(judge_score) or not 0 <= judge_score <= 1
                    ):
                        raise ValueError("LLM judge must return a finite score between 0 and 1")
                final_score = (
                    rule.score if judge_score is None else
                    (1 - self.judge_weight) * rule.score + self.judge_weight * judge_score
                )
                return PromptCaseScore(
                    case.case_id, variant, prompt.version, rule.passed,
                    rule.score, float(judge_score) if judge_score is not None else None,
                    final_score, max(execution.latency_ms, measured_latency), execution.cost,
                )
            except Exception as exc:
                return PromptCaseScore(
                    case.case_id, variant, prompt.version, False, 0.0, None, 0.0,
                    0.0, 0.0, type(exc).__name__,
                )

        tasks = [
            execute_one(case, variant)
            for case in dataset.cases for variant in variants
        ]
        matrix = tuple(await asyncio.gather(*tasks))
        metrics: list[PromptVariantMetrics] = []
        for variant in variants:
            rows = [row for row in matrix if row.variant == variant]
            metrics.append(PromptVariantMetrics(
                variant=variant, version=snapshots[variant].version,
                case_count=len(rows),
                pass_rate=sum(row.passed for row in rows) / len(rows),
                average_score=sum(row.final_score for row in rows) / len(rows),
                average_latency_ms=sum(row.latency_ms for row in rows) / len(rows),
                total_cost=sum(row.cost for row in rows),
            ))
        baseline = next(item for item in metrics if item.variant == baseline_variant)
        candidate = {
            item.variant: item for item in metrics if item.variant != baseline_variant
        }
        # A single comparison payload covers all variants with explicit metric directions.
        comparison = {
            name: compare_metric_reports(
                baseline_variant, name,
                {
                    "pass_rate": baseline.pass_rate,
                    "average_score": baseline.average_score,
                    "latency_ms": baseline.average_latency_ms,
                    "cost_per_run": baseline.total_cost,
                },
                {
                    "pass_rate": item.pass_rate,
                    "average_score": item.average_score,
                    "latency_ms": item.average_latency_ms,
                    "cost_per_run": item.total_cost,
                },
            )
            for name, item in candidate.items()
        }
        report = PromptExperimentReport(
            experiment_id=str(uuid4()), tenant_id=tenant_id, prompt_id=prompt_id,
            dataset_id=dataset.dataset_id, dataset_version=dataset.version,
            baseline_variant=baseline_variant, matrix=matrix,
            metrics=tuple(metrics), comparison=comparison,
            created_at_epoch_ms=int(time.time() * 1000),
        )
        await self.repository.save(report)
        return report


class PostgresPromptExperimentRepository:
    """Append-only PostgreSQL storage for tenant-scoped prompt experiment reports."""

    def __init__(self, connection_factory: Any) -> None:
        self._connect = connection_factory

    async def save(self, report: PromptExperimentReport) -> None:
        if not isinstance(report, PromptExperimentReport):
            raise ValueError("report must be a PromptExperimentReport")
        payload = {
            "experiment_id": report.experiment_id,
            "tenant_id": report.tenant_id,
            "prompt_id": report.prompt_id,
            "dataset_id": report.dataset_id,
            "dataset_version": report.dataset_version,
            "baseline_variant": report.baseline_variant,
            "matrix": [
                {
                    "case_id": row.case_id, "variant": row.variant, "version": row.version,
                    "passed": row.passed, "rule_score": row.rule_score,
                    "judge_score": row.judge_score, "final_score": row.final_score,
                    "latency_ms": row.latency_ms, "cost": row.cost, "error": row.error,
                }
                for row in report.matrix
            ],
            "metrics": [
                {
                    "variant": row.variant, "version": row.version,
                    "case_count": row.case_count, "pass_rate": row.pass_rate,
                    "average_score": row.average_score,
                    "average_latency_ms": row.average_latency_ms, "total_cost": row.total_cost,
                }
                for row in report.metrics
            ],
            "comparison": asdict(report.comparison) if is_dataclass(report.comparison) else report.comparison,
            "created_at_epoch_ms": report.created_at_epoch_ms,
        }
        payload = _sanitize_nan_inf(payload)
        try:
            encoded = json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                allow_nan=False,
                default=_sanitize_json_value,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("experiment report must contain finite JSON values") from exc
        if (
            not isinstance(report.tenant_id, str) or not report.tenant_id.strip()
            or not isinstance(report.experiment_id, str) or not report.experiment_id.strip()
        ):
            raise ValueError("tenant_id and experiment_id are required")
        connection = await self._connect()
        async with connection:
            await connection.execute(
                """INSERT INTO platform_prompt_experiment_reports
                   (tenant_id, experiment_id, prompt_id, dataset_id, dataset_version,
                    baseline_variant, report)
                   VALUES (%s, %s::uuid, %s, %s, %s, %s, %s::jsonb)""",
                (report.tenant_id, report.experiment_id, report.prompt_id, report.dataset_id,
                 report.dataset_version, report.baseline_variant, encoded),
            )

    async def get(self, tenant_id: str, experiment_id: str) -> PromptExperimentReport | None:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id is required")
        if not isinstance(experiment_id, str) or not experiment_id.strip():
            raise ValueError("experiment_id is required")
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT report FROM platform_prompt_experiment_reports
                   WHERE tenant_id=%s AND experiment_id=%s::uuid""",
                (tenant_id, experiment_id),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        payload = row["report"] if isinstance(row, Mapping) else row[0]
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            raise ValueError("stored prompt experiment report is invalid")
        comparison = payload["comparison"]
        if isinstance(comparison, dict) and {"comparison_id", "baseline_run_id", "candidate_run_id", "deltas", "regressions"} <= set(comparison):
            comparison = RegressionComparison(
                comparison_id=comparison["comparison_id"],
                baseline_run_id=comparison["baseline_run_id"],
                candidate_run_id=comparison["candidate_run_id"],
                deltas=tuple(MetricDelta(**item) for item in comparison["deltas"]),
                regressions=tuple(comparison["regressions"]),
            )
        return PromptExperimentReport(
            experiment_id=payload["experiment_id"], tenant_id=payload["tenant_id"],
            prompt_id=payload["prompt_id"], dataset_id=payload["dataset_id"],
            dataset_version=payload["dataset_version"],
            baseline_variant=payload["baseline_variant"],
            matrix=tuple(PromptCaseScore(**item) for item in payload["matrix"]),
            metrics=tuple(PromptVariantMetrics(**item) for item in payload["metrics"]),
            comparison=comparison,
            created_at_epoch_ms=payload["created_at_epoch_ms"],
        )
