from __future__ import annotations

import unittest
from decimal import Decimal

from agent_platform.observability.evaluation_datasets import (
    EvaluationDatasetCase,
    InMemoryEvaluationDatasetRepository,
)
from agent_platform.observability.evaluation_variants import (
    InMemoryVariantEvaluationRepository,
    MultiVariantEvaluationRunService,
    VariantModelOutput,
)


class DeterministicModelExecutor:
    """A fixed model-adapter fixture for isolating eval/scoring repeatability."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str]] = []

    async def run_case(
        self, *, tenant_id: str, model_variant: str, prompt: str, input_text: str,
    ) -> VariantModelOutput:
        self.calls.append((tenant_id, model_variant, prompt, input_text))
        return VariantModelOutput(
            output_text="answer safe",
            input_tokens=12,
            output_tokens=4,
            latency_ms=10.0,
            cost_usd=Decimal("0.0012"),
        )


def _relative_delta_percent(first: float, second: float) -> float:
    denominator = max(abs(first), abs(second))
    if denominator == 0:
        return 0.0
    return abs(first - second) / denominator * 100.0


class EvaluationRepeatabilityAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_eval_twice_has_less_than_five_percent_score_delta(self) -> None:
        datasets = InMemoryEvaluationDatasetRepository()
        dataset = await datasets.create_version(
            tenant_id="tenant-a",
            dataset_id="repeatability",
            cases=(
                EvaluationDatasetCase(
                    "case-exact",
                    "question one",
                    "expected exact answer",
                    {"type": "exact", "pass_threshold": 1.0},
                ),
                EvaluationDatasetCase(
                    "case-keywords",
                    "question two",
                    "answer safe reliable",
                    {
                        "type": "keywords",
                        "keywords": ["answer", "safe", "reliable"],
                        "pass_threshold": 0.6,
                    },
                ),
            ),
            created_by="acceptance-test",
        )
        executor = DeterministicModelExecutor()
        runs = InMemoryVariantEvaluationRepository()
        service = MultiVariantEvaluationRunService(
            dataset_repository=datasets,
            run_repository=runs,
            executor=executor,
            concurrency=1,
        )
        pinned_inputs = {
            "tenant_id": "tenant-a",
            "dataset_id": dataset.dataset_id,
            "dataset_version": dataset.version,
            "prompt": "Answer this support request: {{input}}",
            "model_variants": ["provider/model-a"],
        }

        first = await service.run(**pinned_inputs)
        second = await service.run(**pinned_inputs)

        self.assertNotEqual(first.eval_run_id, second.eval_run_id)
        self.assertEqual(first.dataset_id, second.dataset_id)
        self.assertEqual(first.dataset_version, second.dataset_version)
        self.assertEqual(len(executor.calls), len(dataset.cases) * 2)

        first_variant = first.variant_results[0]
        second_variant = second.variant_results[0]
        self.assertEqual(first_variant.model_variant, second_variant.model_variant)
        self.assertEqual(first_variant.case_count, second_variant.case_count)
        self.assertEqual(
            [case.case_id for case in first_variant.cases],
            [case.case_id for case in second_variant.cases],
        )

        # Quality stability is measured for aggregate and per-case scores.
        self.assertLess(
            _relative_delta_percent(first_variant.average_score, second_variant.average_score),
            5.0,
        )
        self.assertLess(
            _relative_delta_percent(first_variant.pass_rate, second_variant.pass_rate),
            5.0,
        )
        for first_case, second_case in zip(
            first_variant.cases, second_variant.cases, strict=True,
        ):
            self.assertLess(
                _relative_delta_percent(first_case.score, second_case.score),
                5.0,
                msg=f"score drift exceeded 5% for {first_case.case_id}",
            )
            self.assertEqual(first_case.passed, second_case.passed)
            self.assertEqual(first_case.scoring_method, second_case.scoring_method)

        first_record = await runs.get_run(tenant_id="tenant-a", run_id=first.eval_run_id)
        second_record = await runs.get_run(tenant_id="tenant-a", run_id=second.eval_run_id)
        self.assertEqual(first_record["dataset_version"], second_record["dataset_version"])
        self.assertEqual(first_record["prompt_sha256"], second_record["prompt_sha256"])
        self.assertEqual(first_record["variants"], second_record["variants"])


if __name__ == "__main__":
    unittest.main()
