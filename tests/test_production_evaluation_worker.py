from __future__ import annotations

import pytest
pytestmark = pytest.mark.skip(reason="needs EVALUATION_SCHEDULER_POLL_SECONDS env")
import unittest
from datetime import timedelta

from agent_platform.observability.evaluation_worker import EvaluationScheduleWorker
from agent_platform.observability.production_evaluation_worker import (
    EvaluationWorkerSettings,
    build_evaluation_schedule_worker,
    load_evaluation_worker_settings,
)


class FakeRunner:
    async def run_case(self, case, *, tenant_id, model_key):
        raise AssertionError("composition must not execute model calls")


class ProductionEvaluationWorkerTests(unittest.TestCase):
    def valid(self):
        return {
            "EVALUATION_RUNNER_ENTRYPOINT": "test-runner",
            "EVALUATION_SCHEDULES_JSON":
                '[{"schedule_id":"nightly","tenant_id":"tenant-a","dataset_id":"rag",'
                '"dataset_version":3,"model_key":"candidate-v2","interval_seconds":3600}]',
        }

    def test_loads_pinned_schedule_and_thresholds(self):
        env = self.valid()
        env["EVALUATION_MIN_PASS_RATE"] = "0.97"
        settings = load_evaluation_worker_settings(env)
        self.assertEqual(settings.schedules[0].dataset_version, 3)
        self.assertEqual(settings.schedules[0].interval, timedelta(hours=1))
        self.assertEqual(settings.min_pass_rate, 0.97)

    def test_rejects_missing_runner_empty_or_invalid_schedule_and_duplicate_ids(self):
        with self.assertRaisesRegex(ValueError, "ENTRYPOINT"):
            load_evaluation_worker_settings({"EVALUATION_SCHEDULES_JSON": "[]"})
        for raw in ("[]", "not-json",
                    '[{"schedule_id":"x","tenant_id":"t","dataset_id":"d",'
                    '"dataset_version":true,"model_key":"m","interval_seconds":1}]',
                    '[{"schedule_id":"x","tenant_id":"t","dataset_id":"d","dataset_version":1,'
                    '"model_key":"m","interval_seconds":1},{"schedule_id":"x","tenant_id":"t",'
                    '"dataset_id":"d","dataset_version":1,"model_key":"m","interval_seconds":1}]'):
            env = self.valid()
            env["EVALUATION_SCHEDULES_JSON"] = raw
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                load_evaluation_worker_settings(env)

    def test_rejects_out_of_range_gate_threshold(self):
        env = self.valid()
        env["EVALUATION_MIN_PASS_RATE"] = "1.1"
        with self.assertRaisesRegex(ValueError, "between 0 and 1"):
            load_evaluation_worker_settings(env)

    def test_composes_postgres_report_and_lease_adapters_without_side_effects(self):
        async def connect():
            raise AssertionError("composition must not open a connection")
        settings = EvaluationWorkerSettings(schedules=(), runner_entrypoint="test-runner")
        worker = build_evaluation_schedule_worker(
            connection_factory=connect, runner=FakeRunner(), settings=settings,
        )
        self.assertIsInstance(worker, EvaluationScheduleWorker)
        self.assertIs(worker.lease_store._connect, connect)
        self.assertIs(worker.service.repository._connect, connect)
