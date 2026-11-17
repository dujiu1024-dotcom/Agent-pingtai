from __future__ import annotations

import unittest
from decimal import Decimal

from agent_platform.observability.evaluation_datasets import (
    EvaluationDatasetCase, InMemoryEvaluationDatasetRepository,
)
from agent_platform.observability.evaluation_variants import (
    InMemoryVariantEvaluationRepository, MultiVariantEvaluationRunService,
    VariantModelOutput,
)


class InvoiceBackedExecutor:
    """Returns provider usage fields paired with independently recorded invoice fixtures."""

    def __init__(self):
        self.responses = {
            "case-1": VariantModelOutput("ok", 101, 19, 100.0, Decimal("0.001234")),
            "case-2": VariantModelOutput("ok", 203, 37, 250.0, Decimal("0.002345")),
            "case-3": VariantModelOutput("ok", 307, 41, 500.0, Decimal("0.003456")),
        }

    async def run_case(self, *, tenant_id, model_variant, prompt, input_text):
        return self.responses[input_text]


class EvaluationUsageAccuracyAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_usage_aggregates_match_provider_invoice_fixtures_and_persist_exactly(self):
        datasets = InMemoryEvaluationDatasetRepository()
        dataset = await datasets.create_version(
            tenant_id="tenant-a",
            dataset_id="invoice-reconciliation",
            cases=tuple(
                EvaluationDatasetCase(case_id, case_id, "ok", {"type": "exact"})
                for case_id in ("case-1", "case-2", "case-3")
            ),
            created_by="acceptance-test",
        )
        repository = InMemoryVariantEvaluationRepository()
        service = MultiVariantEvaluationRunService(
            dataset_repository=datasets,
            run_repository=repository,
            executor=InvoiceBackedExecutor(),
            concurrency=2,
        )

        result = await service.run(
            tenant_id="tenant-a",
            dataset_id="invoice-reconciliation",
            dataset_version=dataset.version,
            prompt="Return the expected fixture result for {{input}}",
            model_variants=["provider/model-a"],
        )

        aggregate = result.variant_results[0]
        invoice_cost = sum(
            (Decimal("0.001234"), Decimal("0.002345"), Decimal("0.003456")),
            Decimal("0"),
        )
        self.assertEqual(aggregate.input_tokens, 611)
        self.assertEqual(aggregate.output_tokens, 97)
        self.assertEqual(aggregate.total_tokens, 708)
        self.assertEqual(aggregate.cost_usd, str(invoice_cost))
        self.assertEqual(aggregate.average_latency_ms, 850.0 / 3)
        self.assertEqual(aggregate.p50_latency_ms, 250.0)
        self.assertEqual(aggregate.p95_latency_ms, 500.0)

        reported_cost = Decimal(aggregate.cost_usd)
        relative_error = abs(reported_cost - invoice_cost) / invoice_cost
        self.assertLess(relative_error, Decimal("0.01"))

        stored = await repository.get_run(
            tenant_id="tenant-a", run_id=result.eval_run_id,
        )
        persisted = stored["variants"][0]
        self.assertEqual(persisted["input_tokens"], 611)
        self.assertEqual(persisted["output_tokens"], 97)
        self.assertEqual(persisted["total_tokens"], 708)
        self.assertEqual(persisted["cost_usd"], str(invoice_cost))
        self.assertEqual(persisted["average_latency_ms"], 850.0 / 3)
        self.assertEqual(persisted["p50_latency_ms"], 250.0)
        self.assertEqual(persisted["p95_latency_ms"], 500.0)

    async def test_non_finite_provider_latency_is_rejected(self):
        for latency in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(latency=latency):
                with self.assertRaisesRegex(ValueError, "latency_ms"):
                    VariantModelOutput("ok", 1, 1, latency, Decimal("0.01"))


if __name__ == "__main__":
    unittest.main()
