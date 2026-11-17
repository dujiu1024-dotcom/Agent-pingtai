"""Multi-model Eval Runner over immutable tenant-scoped Prompt and Dataset inputs."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import statistics
import time
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Protocol, Sequence
from uuid import uuid4

from agent_platform.observability.evaluation_datasets import EvaluationDatasetVersion

_MAX_VARIANTS = 16
_MAX_CONCURRENCY = 32
_PROMPT_LIMIT = 100_000
_MODEL_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


@dataclass(frozen=True)
class VariantModelOutput:
    output_text: str
    input_tokens: int
    output_tokens: int
    latency_ms: float | None = None
    cost_usd: Decimal | str | int | float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.output_text, str):
            raise ValueError("model output_text must be a string")
        for name, value in (("input_tokens", self.input_tokens), ("output_tokens", self.output_tokens)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.latency_ms is not None and (
            isinstance(self.latency_ms, bool) or not isinstance(self.latency_ms, (int, float))
            or not math.isfinite(self.latency_ms) or self.latency_ms < 0
        ):
            raise ValueError("latency_ms must be a finite non-negative number")
        if self.cost_usd is not None:
            try:
                cost = Decimal(str(self.cost_usd))
            except (InvalidOperation, ValueError):
                raise ValueError("cost_usd must be a non-negative decimal") from None
            if not cost.is_finite() or cost < 0:
                raise ValueError("cost_usd must be a non-negative decimal")


class VariantModelExecutor(Protocol):
    async def run_case(
        self, *, tenant_id: str, model_variant: str, prompt: str, input_text: str,
    ) -> VariantModelOutput: ...


class RubricJudge(Protocol):
    async def score(
        self, *, rubric: Mapping[str, Any], prompt: str, input_text: str,
        expected_output: str, actual_output: str, model_variant: str,
    ) -> float: ...


class VariantEvaluationRepository(Protocol):
    async def save_run(self, *, tenant_id: str, run: Mapping[str, Any]) -> str: ...
    async def get_run(self, *, tenant_id: str, run_id: str) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class VariantCaseScore:
    case_id: str
    score: float
    passed: bool
    scoring_method: str


@dataclass(frozen=True)
class VariantAggregate:
    model_variant: str
    case_count: int
    passed_count: int
    pass_rate: float
    average_score: float
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: str | None
    average_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    cases: tuple[VariantCaseScore, ...]


@dataclass(frozen=True)
class MultiVariantEvaluationResult:
    eval_run_id: str
    dataset_id: str
    dataset_version: int
    variant_results: tuple[VariantAggregate, ...]


def _score_rule(rubric: Mapping[str, Any], expected: str, actual: str) -> tuple[float, str]:
    """Small deterministic rubric engine; advanced rubrics use the injected judge."""
    kind = rubric.get("type", "exact")
    if not isinstance(kind, str):
        raise ValueError("rubric type must be a string")
    if kind == "exact":
        score = 1.0 if actual.strip() == expected.strip() else 0.0
    elif kind == "contains":
        score = 1.0 if expected.casefold() in actual.casefold() else 0.0
    elif kind in {"keywords", "keyword"}:
        terms = rubric.get("keywords")
        if not isinstance(terms, list) or not terms or any(not isinstance(x, str) or not x for x in terms):
            raise ValueError("keyword rubric requires a non-empty keywords array")
        score = sum(term.casefold() in actual.casefold() for term in terms) / len(terms)
    elif kind == "llm_judge":
        raise ValueError("llm_judge rubric requires a configured judge")
    elif kind == "custom":
        raise ValueError("custom rubric requires a configured judge")
    else:
        raise ValueError("unsupported rubric type")
    return score, "rule"


class MultiVariantEvaluationRunService:
    """Run a pinned dataset against multiple model variants and persist measured usage."""

    def __init__(
        self, *, dataset_repository: Any, run_repository: VariantEvaluationRepository,
        executor: VariantModelExecutor, judge: RubricJudge | None = None,
        concurrency: int = 4,
    ) -> None:
        if isinstance(concurrency, bool) or not isinstance(concurrency, int) or not 1 <= concurrency <= _MAX_CONCURRENCY:
            raise ValueError("concurrency must be an integer between 1 and 32")
        self.dataset_repository = dataset_repository
        self.run_repository = run_repository
        self.executor = executor
        self.judge = judge
        self.concurrency = concurrency

    async def run(
        self, *, tenant_id: str, dataset_id: str, dataset_version: int,
        prompt: str, model_variants: Sequence[str],
    ) -> MultiVariantEvaluationResult:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id is required")
        if not isinstance(dataset_id, str) or not dataset_id.strip():
            raise ValueError("dataset_id is required")
        if isinstance(dataset_version, bool) or not isinstance(dataset_version, int) or dataset_version < 1:
            raise ValueError("dataset_version must be a positive integer")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > _PROMPT_LIMIT:
            raise ValueError("prompt must be a non-empty string of at most 100000 characters")
        if not isinstance(model_variants, Sequence) or isinstance(model_variants, (str, bytes)):
            raise ValueError("model_variants must be a list")
        variants = tuple(model_variants)
        if not 1 <= len(variants) <= _MAX_VARIANTS:
            raise ValueError("model_variants must contain 1 to 16 unique variants")
        if any(not isinstance(key, str) or not _MODEL_KEY.fullmatch(key) for key in variants):
            raise ValueError("each model variant must be a valid model key")
        if len(set(variants)) != len(variants):
            raise ValueError("model_variants must contain unique variants")

        dataset: EvaluationDatasetVersion = await self.dataset_repository.get_version(
            tenant_id=tenant_id, dataset_id=dataset_id, version=dataset_version,
        )
        semaphore = asyncio.Semaphore(self.concurrency)

        async def run_one(variant: str) -> VariantAggregate:
            async def run_case(case: Any) -> tuple[VariantCaseScore, VariantModelOutput, float]:
                rendered_prompt = prompt.replace("{{input}}", case.input)
                async with semaphore:
                    started = time.perf_counter()
                    output = await self.executor.run_case(
                        tenant_id=tenant_id, model_variant=variant,
                        prompt=rendered_prompt, input_text=case.input,
                    )
                    elapsed_ms = (time.perf_counter() - started) * 1000
                if not isinstance(output, VariantModelOutput):
                    raise TypeError("model executor must return VariantModelOutput")
                rubric_kind = case.rubric.get("type", "exact")
                if rubric_kind in {"llm_judge", "custom"}:
                    if self.judge is None:
                        raise ValueError(f"{rubric_kind} rubric requires a configured judge")
                    score = await self.judge.score(
                        rubric=case.rubric, prompt=rendered_prompt, input_text=case.input,
                        expected_output=case.expected_output, actual_output=output.output_text,
                        model_variant=variant,
                    )
                    if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 1:
                        raise ValueError("judge score must be between 0 and 1")
                    threshold = case.rubric.get("pass_threshold", 1.0)
                    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1:
                        raise ValueError("pass_threshold must be between 0 and 1")
                    scored = VariantCaseScore(
                        case.case_id, float(score), score >= threshold,
                        "llm_judge" if rubric_kind == "llm_judge" else "custom",
                    )
                else:
                    score, method = _score_rule(case.rubric, case.expected_output, output.output_text)
                    threshold = case.rubric.get("pass_threshold", 1.0)
                    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1:
                        raise ValueError("pass_threshold must be between 0 and 1")
                    scored = VariantCaseScore(case.case_id, score, score >= threshold, method)
                return scored, output, float(output.latency_ms if output.latency_ms is not None else elapsed_ms)

            rows = await asyncio.gather(*(run_case(case) for case in dataset.cases))
            scores = tuple(row[0] for row in rows)
            outputs = [row[1] for row in rows]
            latencies = [row[2] for row in rows]
            costs = [Decimal(str(item.cost_usd)) for item in outputs if item.cost_usd is not None]
            total_cost = sum(costs, Decimal("0")) if len(costs) == len(outputs) else None
            return VariantAggregate(
                model_variant=variant, case_count=len(scores),
                passed_count=sum(item.passed for item in scores),
                pass_rate=sum(item.passed for item in scores) / len(scores),
                average_score=statistics.fmean(item.score for item in scores),
                input_tokens=sum(item.input_tokens for item in outputs),
                output_tokens=sum(item.output_tokens for item in outputs),
                total_tokens=sum(item.input_tokens + item.output_tokens for item in outputs),
                cost_usd=str(total_cost) if total_cost is not None else None,
                average_latency_ms=statistics.fmean(latencies),
                p50_latency_ms=float(statistics.median(latencies)),
                p95_latency_ms=sorted(latencies)[max(0, math.ceil(len(latencies) * .95) - 1)],
                cases=scores,
            )

        aggregates = tuple(await asyncio.gather(*(run_one(variant) for variant in variants)))
        run_id = str(uuid4())
        payload = {
            "eval_run_id": run_id, "dataset_id": dataset.dataset_id,
            "dataset_version": dataset.version,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "variants": [{
                **asdict(item),
                "cases": [asdict(case) for case in item.cases],
            } for item in aggregates],
        }
        persisted_id = await self.run_repository.save_run(tenant_id=tenant_id, run=payload)
        if not isinstance(persisted_id, str) or not persisted_id.strip():
            raise RuntimeError("evaluation repository did not return a run ID")
        return MultiVariantEvaluationResult(run_id, dataset.dataset_id, dataset.version, aggregates)


class InMemoryVariantEvaluationRepository:
    def __init__(self) -> None:
        self._runs: dict[tuple[str, str], Mapping[str, Any]] = {}

    async def save_run(self, *, tenant_id: str, run: Mapping[str, Any]) -> str:
        if not tenant_id.strip() or not isinstance(run, Mapping):
            raise ValueError("tenant_id and run are required")
        run_id = run.get("eval_run_id")
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("eval_run_id is required")
        key = (tenant_id, run_id)
        if key in self._runs:
            raise ValueError("evaluation run already exists")
        self._runs[key] = json.loads(json.dumps(dict(run), ensure_ascii=False))
        return run_id

    async def get_run(self, *, tenant_id: str, run_id: str) -> Mapping[str, Any]:
        row = self._runs.get((tenant_id, run_id))
        if row is None:
            raise KeyError("evaluation run not found")
        return json.loads(json.dumps(dict(row), ensure_ascii=False))


class PostgresVariantEvaluationRepository:
    def __init__(self, connection_factory: Any) -> None:
        self._connect = connection_factory

    async def save_run(self, *, tenant_id: str, run: Mapping[str, Any]) -> str:
        from psycopg.types.json import Jsonb
        run_id = run.get("eval_run_id")
        if not tenant_id.strip() or not isinstance(run_id, str) or not run_id:
            raise ValueError("tenant_id and eval_run_id are required")
        connection = await self._connect()
        async with connection:
            await connection.execute(
                """INSERT INTO platform_eval_variant_runs
                   (tenant_id,eval_run_id,dataset_id,dataset_version,prompt_sha256,results)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (tenant_id,eval_run_id) DO NOTHING""",
                (tenant_id, run_id, run["dataset_id"], run["dataset_version"],
                 run["prompt_sha256"], Jsonb(dict(run))),
            )
        return run_id

    async def get_run(self, *, tenant_id: str, run_id: str) -> Mapping[str, Any]:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT results FROM platform_eval_variant_runs
                   WHERE tenant_id=%s AND eval_run_id=%s""", (tenant_id, run_id),
            )
            row = await cursor.fetchone()
        if row is None:
            raise KeyError("evaluation run not found")
        value = row["results"]
        return value if isinstance(value, dict) else json.loads(value)
