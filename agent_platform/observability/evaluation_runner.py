"""Orchestrate reproducible evaluation runs and persist release-gate evidence."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from agent_platform.observability.evaluation import (
    EvalCase,
    EvalDataset,
    EvalGatePolicy,
    EvalReport,
    EvalResult,
    build_report,
    evaluate_case,
    gate_report,
)


@dataclass(frozen=True)
class EvalOutput:
    output_text: str
    retrieved_document_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvaluationRunResult:
    eval_run_id: str
    report: EvalReport
    gate_passed: bool
    gate_failures: tuple[str, ...]


class EvaluationRepository(Protocol):
    async def load_dataset(self, dataset_id: str, version: int) -> EvalDataset: ...
    async def save_report(
        self,
        report: EvalReport,
        *,
        model_key: str,
        gate_passed: bool,
        gate_failures: tuple[str, ...],
        tenant_id: str = "",
    ) -> str: ...


class EvaluationCaseRunner(Protocol):
    async def run_case(
        self, case: EvalCase, *, tenant_id: str, model_key: str,
    ) -> EvalOutput: ...


class EvaluationRunService:
    """Run a pinned dataset against one model and persist immutable gate evidence."""

    def __init__(
        self,
        *,
        repository: EvaluationRepository,
        runner: EvaluationCaseRunner,
        policy: EvalGatePolicy,
        concurrency: int = 4,
        k: int = 5,
    ) -> None:
        if isinstance(concurrency, bool) or not isinstance(concurrency, int) or not 1 <= concurrency <= 32:
            raise ValueError("concurrency must be an integer between 1 and 32")
        if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= 100:
            raise ValueError("k must be an integer between 1 and 100")
        self.repository = repository
        self.runner = runner
        self.policy = policy
        self.concurrency = concurrency
        self.k = k

    async def run(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        dataset_version: int,
        model_key: str,
    ) -> EvaluationRunResult:
        if any(not isinstance(value, str) or not value.strip() for value in (
            tenant_id, dataset_id, model_key,
        )):
            raise ValueError("tenant_id, dataset_id and model_key are required")
        if isinstance(dataset_version, bool) or not isinstance(dataset_version, int) or dataset_version < 1:
            raise ValueError("dataset_version must be a positive integer")

        dataset = await self.repository.load_dataset(dataset_id, dataset_version)
        if not isinstance(dataset, EvalDataset):
            raise TypeError("evaluation repository returned an invalid dataset")
        semaphore = asyncio.Semaphore(self.concurrency)

        async def evaluate(case: EvalCase) -> EvalResult:
            async with semaphore:
                output = await self.runner.run_case(
                    case, tenant_id=tenant_id, model_key=model_key,
                )
            if not isinstance(output, EvalOutput) or not isinstance(output.output_text, str):
                raise TypeError("evaluation runner must return EvalOutput with output_text")
            if any(not isinstance(doc_id, str) or not doc_id.strip()
                   for doc_id in output.retrieved_document_ids):
                raise ValueError("retrieved document IDs must be non-empty strings")
            return evaluate_case(
                case,
                output_text=output.output_text,
                retrieved_document_ids=output.retrieved_document_ids,
                k=self.k,
            )

        results = await asyncio.gather(*(evaluate(case) for case in dataset.cases))
        report = build_report(dataset, results)
        passed, failures = gate_report(report, self.policy)
        run_id = await self.repository.save_report(
            report,
            model_key=model_key,
            gate_passed=passed,
            gate_failures=failures,
            tenant_id=tenant_id,
        )
        if not isinstance(run_id, str) or not run_id.strip():
            raise RuntimeError("evaluation repository did not return a run ID")
        return EvaluationRunResult(run_id, report, passed, failures)
