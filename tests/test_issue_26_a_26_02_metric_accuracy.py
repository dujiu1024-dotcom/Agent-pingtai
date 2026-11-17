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


class MetricOracleExecutor:
    """Fixture with independently declared usage and latency ground truth."""

    MEASUREMENTS = {
        "variant-a": {
            "case-1": (101, 11, "0.000123", 101.25),
            "case-2": (203, 23, "0.000234", 203.50),
            "case-3": (307, 37, "0.000345", 307.75),
        },
        "variant-b": {
            "case-1": (1010, 110, "0.001111", 1000.5),
            "case-2": (2030, 230, "0.002222", 2000.5),
            "case-3": (3070, 370, "0.003333", 3000.5),
        },
    }

    async def run_case(self, *, tenant_id, model_variant, prompt, input_text):
        case_id = input_text
        input_tokens, output_tokens, cost, latency = self.MEASUREMENTS[model_variant][case_id]
        return VariantModelOutput(
            "accepted", input_tokens, output_tokens, latency, Decimal(cost),
        )


def relative_error_percent(actual, expected):
    actual_value, expected_value = Decimal(str(actual)), Decimal(str(expected))
    if expected_value == 0:
        return Decimal(0) if actual_value == 0 else Decimal("Infinity")
    return abs(actual_value - expected_value) / abs(expected_value) * Decimal(100)


class EvaluationMetricAccuracyAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_cost_tokens_and_latency_statistics_are_within_one_percent(self):
        datasets = InMemoryEvaluationDatasetRepository()
        dataset = await datasets.create_version(
            tenant_id="tenant-metrics",
            dataset_id="metric-accuracy",
            cases=tuple(
                EvaluationDatasetCase(
                    case_id=f"case-{index}",
                    input=f"case-{index}",
                    expected_output="accepted",
                    rubric={"type": "exact"},
                )
                for index in range(1, 4)
            ),
            created_by="acceptance-test",
        )
        repository = InMemoryVariantEvaluationRepository()
        service = MultiVariantEvaluationRunService(
            dataset_repository=datasets,
            run_repository=repository,
            executor=MetricOracleExecutor(),
            concurrency=3,
        )

        result = await service.run(
            tenant_id="tenant-metrics",
            dataset_id="metric-accuracy",
            dataset_version=dataset.version,
            prompt="{{input}}",
            model_variants=("variant-a", "variant-b"),
        )

        for aggregate in result.variant_results:
            measurements = MetricOracleExecutor.MEASUREMENTS[aggregate.model_variant]
            expected_input_tokens = sum(item[0] for item in measurements.values())
            expected_output_tokens = sum(item[1] for item in measurements.values())
            expected_total_tokens = expected_input_tokens + expected_output_tokens
            expected_cost = sum(
                (Decimal(item[2]) for item in measurements.values()), Decimal(0),
            )
            latencies = sorted(item[3] for item in measurements.values())
            expected_average_latency = sum(latencies) / len(latencies)
            expected_p50_latency = latencies[1]
            expected_p95_latency = latencies[2]

            comparisons = {
                "input_tokens": (aggregate.input_tokens, expected_input_tokens),
                "output_tokens": (aggregate.output_tokens, expected_output_tokens),
                "total_tokens": (aggregate.total_tokens, expected_total_tokens),
                "cost_usd": (aggregate.cost_usd, expected_cost),
                "average_latency_ms": (aggregate.average_latency_ms, expected_average_latency),
                "p50_latency_ms": (aggregate.p50_latency_ms, expected_p50_latency),
                "p95_latency_ms": (aggregate.p95_latency_ms, expected_p95_latency),
            }
            for metric, (actual, expected) in comparisons.items():
                with self.subTest(variant=aggregate.model_variant, metric=metric):
                    self.assertLess(
                        relative_error_percent(actual, expected),
                        Decimal("1"),
                        f"{metric} error must be strictly below 1%",
                    )

            self.assertEqual(aggregate.cost_usd, str(expected_cost))
            self.assertEqual(aggregate.case_count, 3)

        persisted = await repository.get_run(
            tenant_id="tenant-metrics", run_id=result.eval_run_id,
        )
        self.assertEqual(len(persisted["variants"]), 2)
        for aggregate, stored in zip(result.variant_results, persisted["variants"]):
            for field in (
                "input_tokens", "output_tokens", "total_tokens", "cost_usd",
                "average_latency_ms", "p50_latency_ms", "p95_latency_ms",
            ):
                with self.subTest(variant=aggregate.model_variant, persisted_field=field):
                    self.assertEqual(stored[field], getattr(aggregate, field))

    def test_non_finite_latency_is_rejected(self):
        for latency in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(latency=latency):
                with self.assertRaisesRegex(ValueError, "finite non-negative"):
                    VariantModelOutput("answer", 1, 1, latency)


if __name__ == "__main__":
    unittest.main()
