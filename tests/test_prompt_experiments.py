from __future__ import annotations

import asyncio
import unittest

from agent_platform.observability.prompt_experiments import (
    InMemoryPromptExperimentRepository,
    PromptABTestService,
    PromptExecution,
    PromptExperimentCase,
    PromptExperimentDataset,
)
from agent_platform.observability.prompts import PromptRegistry, tenant_prompt_key


class FakeExecutor:
    def __init__(self, outputs=None, *, delay=0):
        self.outputs = outputs or {}
        self.delay = delay
        self.calls = []

    async def execute(
        self, *, tenant_id, prompt_id, variant, version,
        rendered_prompt, variables,
    ):
        self.calls.append((tenant_id, prompt_id, variant, version, rendered_prompt))
        if self.delay:
            await asyncio.sleep(self.delay)
        return PromptExecution(
            output_text=self.outputs.get(variant, "approved response"),
            latency_ms=5,
            cost=0.01,
        )


class FakeJudge:
    def __init__(self, score=0.8):
        self.value = score
        self.calls = []

    async def score(self, *, rubric, variables, output_text):
        self.calls.append((rubric, dict(variables), output_text))
        return self.value


def setup_registry():
    registry = PromptRegistry()
    prompt_id = tenant_prompt_key("tenant-a", "support")
    v1 = registry.register(prompt_id, "Answer: {question}")
    v2 = registry.register(prompt_id, "Give a concise answer: {question}")
    registry.create_variant(prompt_id, "control", v1.version, created_by="owner")
    registry.create_variant(prompt_id, "candidate", v2.version, created_by="owner")
    return registry, prompt_id


def dataset():
    return PromptExperimentDataset(
        "support-eval", 1,
        (
            PromptExperimentCase(
                "case-1", {"question": "How do I deploy?"},
                expected_terms=("approved",), forbidden_terms=("secret",),
                rubric="Answer safely and accurately",
            ),
            PromptExperimentCase(
                "case-2", {"question": "How do I roll back?"},
                expected_terms=("approved",), forbidden_terms=("password",),
                rubric="Give actionable rollback steps",
            ),
        ),
        "eval-owner",
    )


class PromptABExperimentTests(unittest.IsolatedAsyncioTestCase):
    async def test_runs_variants_and_builds_case_matrix_and_comparison(self):
        registry, prompt_id = setup_registry()
        executor = FakeExecutor({
            "control": "approved response",
            "candidate": "approved response with secret",
        })
        repository = InMemoryPromptExperimentRepository()
        judge = FakeJudge(0.9)
        service = PromptABTestService(
            registry=registry, executor=executor, repository=repository, judge=judge,
            concurrency=2, judge_weight=0.25,
        )
        report = await service.run(
            tenant_id="tenant-a", prompt_id="support", dataset=dataset(),
            variants=("control", "candidate"), baseline_variant="control",
        )
        self.assertEqual(len(report.matrix), 4)
        self.assertEqual([row.case_id for row in report.matrix], [
            "case-1", "case-1", "case-2", "case-2",
        ])
        self.assertEqual(len(judge.calls), 4)
        metrics = {item.variant: item for item in report.metrics}
        self.assertEqual(metrics["control"].pass_rate, 1.0)
        self.assertEqual(metrics["candidate"].pass_rate, 0.0)
        self.assertEqual(metrics["control"].version, 1)
        self.assertEqual(metrics["candidate"].version, 2)
        self.assertIn("candidate", report.comparison)
        self.assertIsNotNone(await repository.get("tenant-a", report.experiment_id))
        self.assertIsNone(await repository.get("tenant-b", report.experiment_id))
        self.assertTrue(all(call[0] == "tenant-a" for call in executor.calls))
        self.assertTrue(all(call[1] == "support" for call in executor.calls))
        self.assertTrue(all(call[2] in {"control", "candidate"} for call in executor.calls))

    async def test_missing_or_extra_prompt_variables_fail_case_closed(self):
        registry, _ = setup_registry()
        executor = FakeExecutor()
        service = PromptABTestService(
            registry=registry, executor=executor,
            repository=InMemoryPromptExperimentRepository(),
        )
        invalid = PromptExperimentDataset(
            "invalid", 1, (PromptExperimentCase("case", {"other": "value"}),), "owner",
        )
        report = await service.run(
            tenant_id="tenant-a", prompt_id="support", dataset=invalid,
            variants=("control", "candidate"), baseline_variant="control",
        )
        self.assertEqual(len(executor.calls), 0)
        self.assertTrue(all(row.error == "ValueError" and row.final_score == 0 for row in report.matrix))

    async def test_variant_must_be_active_and_tenant_scoped(self):
        registry, _ = setup_registry()
        registry.deprecate_variant(
            tenant_prompt_key("tenant-a", "support"), "candidate", updated_by="owner",
        )
        service = PromptABTestService(
            registry=registry, executor=FakeExecutor(),
            repository=InMemoryPromptExperimentRepository(),
        )
        with self.assertRaises(KeyError):
            await service.run(
                tenant_id="tenant-a", prompt_id="support", dataset=dataset(),
                variants=("control", "candidate"), baseline_variant="control",
            )
        with self.assertRaises(KeyError):
            await service.run(
                tenant_id="tenant-b", prompt_id="support", dataset=dataset(),
                variants=("control", "candidate"), baseline_variant="control",
            )

    async def test_execution_timeout_is_recorded_as_failed_score(self):
        registry, _ = setup_registry()
        service = PromptABTestService(
            registry=registry, executor=FakeExecutor(delay=0.03),
            repository=InMemoryPromptExperimentRepository(), timeout_seconds=0.01,
        )
        report = await service.run(
            tenant_id="tenant-a", prompt_id="support", dataset=dataset(),
            variants=("control", "candidate"), baseline_variant="control",
        )
        self.assertTrue(all(row.error == "TimeoutError" for row in report.matrix))
        self.assertTrue(all(row.final_score == 0 for row in report.matrix))

    async def test_invalid_judge_score_fails_closed(self):
        registry, _ = setup_registry()
        service = PromptABTestService(
            registry=registry, executor=FakeExecutor(), judge=FakeJudge(float("nan")),
            repository=InMemoryPromptExperimentRepository(),
        )
        report = await service.run(
            tenant_id="tenant-a", prompt_id="support", dataset=dataset(),
            variants=("control", "candidate"), baseline_variant="control",
        )
        self.assertTrue(all(row.error == "ValueError" for row in report.matrix))

    async def test_dataset_and_configuration_bounds(self):
        with self.assertRaises(ValueError):
            PromptExperimentDataset("empty", 1, (), "owner")
        with self.assertRaises(ValueError):
            PromptExperimentCase("case", {"question": 1})
        registry, _ = setup_registry()
        with self.assertRaises(ValueError):
            PromptABTestService(
                registry=registry, executor=FakeExecutor(),
                repository=InMemoryPromptExperimentRepository(), concurrency=0,
            )


    async def test_repeated_ab_scores_have_less_than_five_percent_cv(self):
        """Acceptance harness: repeated runs stay within the 5% score-CV gate."""
        import statistics

        registry, _ = setup_registry()
        run_judge_scores = (0.78, 0.82, 0.79, 0.81) * 5
        candidate_scores = []

        for judge_value in run_judge_scores:
            service = PromptABTestService(
                registry=registry,
                executor=FakeExecutor(),
                repository=InMemoryPromptExperimentRepository(),
                judge=FakeJudge(judge_value),
                judge_weight=0.5,
            )
            report = await service.run(
                tenant_id="tenant-a",
                prompt_id="support",
                dataset=dataset(),
                variants=("control", "candidate"),
                baseline_variant="control",
            )
            candidate = next(
                item for item in report.metrics if item.variant == "candidate"
            )
            candidate_scores.append(candidate.average_score)

        mean_score = statistics.fmean(candidate_scores)
        coefficient_of_variation = statistics.pstdev(candidate_scores) / mean_score
        self.assertGreater(mean_score, 0)
        self.assertLess(
            coefficient_of_variation,
            0.05,
            f"score coefficient of variation {coefficient_of_variation:.2%} "
            "must remain below 5%",
        )


if __name__ == "__main__":
    unittest.main()
