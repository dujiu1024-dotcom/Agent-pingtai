from __future__ import annotations

import asyncio
import unittest
from decimal import Decimal

from agent_platform.observability.evaluation_datasets import (
    EvaluationDatasetCase, InMemoryEvaluationDatasetRepository,
)
from agent_platform.observability.evaluation_variants import (
    InMemoryVariantEvaluationRepository, MultiVariantEvaluationRunService,
    VariantModelOutput,
)


class FakeExecutor:
    def __init__(self):
        self.calls = []
        self.active = 0
        self.max_active = 0

    async def run_case(self, *, tenant_id, model_variant, prompt, input_text):
        self.calls.append((tenant_id, model_variant, prompt, input_text))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0)
        self.active -= 1
        text = "answer " + model_variant
        return VariantModelOutput(text, 10, 5, 12.5, Decimal("0.0003"))


class FakeJudge:
    async def score(self, **kwargs):
        return 0.75


class MultiVariantEvaluationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.datasets = InMemoryEvaluationDatasetRepository()
        self.dataset = await self.datasets.create_version(
            tenant_id="tenant-a", dataset_id="support",
            cases=(
                EvaluationDatasetCase("c1", "question one", "answer", {"type": "contains", "pass_threshold": 1}),
                EvaluationDatasetCase("c2", "question two", "answer", {"type": "keywords", "keywords": ["answer", "safe"], "pass_threshold": 0.5}),
            ), created_by="user-a",
        )
        self.runs = InMemoryVariantEvaluationRepository()
        self.executor = FakeExecutor()

    async def test_same_pinned_prompt_dataset_runs_all_variants_and_records_usage(self):
        service = MultiVariantEvaluationRunService(
            dataset_repository=self.datasets, run_repository=self.runs,
            executor=self.executor, concurrency=2,
        )
        result = await service.run(
            tenant_id="tenant-a", dataset_id="support", dataset_version=self.dataset.version,
            prompt="Please answer: {{input}}", model_variants=["model-a", "model-b"],
        )
        self.assertEqual([row.model_variant for row in result.variant_results], ["model-a", "model-b"])
        for row in result.variant_results:
            self.assertEqual(row.input_tokens, 20)
            self.assertEqual(row.output_tokens, 10)
            self.assertEqual(row.total_tokens, 30)
            self.assertEqual(row.cost_usd, "0.0006")
            self.assertEqual(row.average_latency_ms, 12.5)
            self.assertEqual(row.case_count, 2)
        self.assertEqual(len(self.executor.calls), 4)
        stored = await self.runs.get_run(tenant_id="tenant-a", run_id=result.eval_run_id)
        self.assertEqual(stored["dataset_version"], 1)
        self.assertNotIn("prompt", stored)
        self.assertEqual(self.executor.max_active, 2)

    async def test_llm_judge_and_custom_rubric_are_supported(self):
        await self.datasets.create_version(
            tenant_id="tenant-a", dataset_id="judge",
            cases=(
                EvaluationDatasetCase("c1", "q", "a", {"type": "llm_judge", "pass_threshold": 0.7}),
                EvaluationDatasetCase("c2", "q2", "a2", {"type": "custom", "pass_threshold": 0.7}),
            ), created_by="user-a",
        )
        service = MultiVariantEvaluationRunService(
            dataset_repository=self.datasets, run_repository=self.runs,
            executor=self.executor, judge=FakeJudge(),
        )
        result = await service.run(
            tenant_id="tenant-a", dataset_id="judge", dataset_version=1,
            prompt="{{input}}", model_variants=["model-a"],
        )
        self.assertTrue(all(case.passed for case in result.variant_results[0].cases))

    async def test_tenant_isolation_and_missing_cost_are_explicit(self):
        with self.assertRaises(KeyError):
            await self.datasets.get_version(tenant_id="tenant-b", dataset_id="support", version=1)
        output = VariantModelOutput("answer", 1, 2)
        self.assertIsNone(output.cost_usd)

    async def test_invalid_variants_and_rubric_fail_closed(self):
        service = MultiVariantEvaluationRunService(
            dataset_repository=self.datasets, run_repository=self.runs,
            executor=self.executor,
        )
        with self.assertRaisesRegex(ValueError, "unique"):
            await service.run(
                tenant_id="tenant-a", dataset_id="support", dataset_version=1,
                prompt="x", model_variants=["same", "same"],
            )
        await self.datasets.create_version(
            tenant_id="tenant-a", dataset_id="bad",
            cases=(EvaluationDatasetCase("c", "q", "a", {"type": "llm_judge"}),),
            created_by="user-a",
        )
        with self.assertRaisesRegex(ValueError, "configured judge"):
            await service.run(
                tenant_id="tenant-a", dataset_id="bad", dataset_version=1,
                prompt="x", model_variants=["model-a"],
            )


if __name__ == "__main__":
    unittest.main()
