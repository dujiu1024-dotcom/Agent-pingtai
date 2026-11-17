"""PostgreSQL tenant-isolation, idempotency and concurrency tests for Issue 16.

Requires a disposable PostgreSQL database configured with
AGENT_PLATFORM_TEST_DSN. Migrations are applied using the repository's normal
integration-test convention.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

DSN = os.environ.get("AGENT_PLATFORM_TEST_DSN")

from agent_platform.observability.alerts import AlertEvent
from agent_platform.observability.alert_store import PostgresAlertEventStore
from agent_platform.observability.evaluation import EvalCase, EvalDataset, EvalReport, EvalResult
from agent_platform.observability.evaluation_store import PostgresEvaluationRepository
from agent_platform.observability.prompt_rollouts import (
    PostgresPromptRolloutRepository,
    PromptRolloutPolicy,
    PromptRouteDecision,
)
from agent_platform.observability.prompt_store import PostgresPromptRegistry
from agent_platform.observability.prompts import tenant_prompt_key

if DSN:
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row


@unittest.skipUnless(DSN, "AGENT_PLATFORM_TEST_DSN is not configured")
class Issue16ObservabilityPostgresReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        migration_dir = Path("agent_platform/storage/postgres")
        async with await AsyncConnection.connect(DSN, autocommit=True) as connection:
            for migration_path in sorted(migration_dir.glob("*.sql")):
                await connection.execute(migration_path.read_text(encoding="utf-8"), prepare=False)

        async def connect():
            return await AsyncConnection.connect(DSN, row_factory=dict_row)

        self.connect = connect
        suffix = uuid4().hex
        self.tenant_a = f"issue16-a-{suffix}"
        self.tenant_b = f"issue16-b-{suffix}"
        self.alerts = PostgresAlertEventStore(connect)
        self.evaluations = PostgresEvaluationRepository(connect)
        self.prompts = PostgresPromptRegistry(connect)
        self.rollouts = PostgresPromptRolloutRepository(connect)

    def make_alert(self) -> AlertEvent:
        return AlertEvent(
            rule_id="high-error-rate",
            metric_name="agent.error_rate",
            observed_value=0.9,
            threshold=0.8,
            severity="critical",
            fired_at=datetime.now(timezone.utc),
            description="synthetic integration event",
        )

    async def test_alert_deduplication_is_atomic_under_concurrent_writers_and_tenant_scoped(self):
        dedupe_key = f"same-delivery-{uuid4().hex}"
        writes = await asyncio.gather(*(
            self.alerts.record_once(
                self.make_alert(), scope_key=self.tenant_a, dedupe_key=dedupe_key,
            )
            for _ in range(8)
        ))
        event_ids = {event_id for event_id, _created in writes}
        self.assertEqual(len(event_ids), 1)
        self.assertEqual(sum(created for _event_id, created in writes), 1)

        other_tenant_id, created = await self.alerts.record_once(
            self.make_alert(), scope_key=self.tenant_b, dedupe_key=dedupe_key,
        )
        self.assertTrue(created)
        self.assertNotIn(other_tenant_id, event_ids)
        self.assertEqual(
            {str(row["event_id"]) for row in await self.alerts.list_open(scope_key=self.tenant_a)},
            event_ids,
        )
        self.assertEqual(
            {str(row["event_id"]) for row in await self.alerts.list_open(scope_key=self.tenant_b)},
            {other_tenant_id},
        )

    async def test_alert_outbox_skip_locked_claim_and_retry_are_single_owner(self):
        event_id, created = await self.alerts.record_once(
            self.make_alert(),
            scope_key=self.tenant_a,
            dedupe_key=f"claim-{uuid4().hex}",
        )
        self.assertTrue(created)
        batches = await asyncio.gather(
            self.alerts.claim_pending_deliveries(limit=10, event_id=event_id),
            self.alerts.claim_pending_deliveries(limit=10, event_id=event_id),
        )
        claimed = [row for batch in batches for row in batch]
        self.assertEqual(len(claimed), 1)
        self.assertEqual(str(claimed[0]["event_id"]), event_id)
        self.assertEqual(claimed[0]["delivery_attempts"], 1)

        await self.alerts.mark_delivery(
            event_id, success=False, now=datetime.now(timezone.utc),
            error="temporary provider failure",
        )
        async with await self.connect() as connection:
            await connection.execute(
                "UPDATE platform_alert_events SET next_delivery_at=now() WHERE event_id=%s",
                (event_id,),
            )
        retry = await self.alerts.claim_pending_deliveries(limit=10, event_id=event_id)
        self.assertEqual(len(retry), 1)
        self.assertEqual(retry[0]["delivery_attempts"], 2)
        await self.alerts.mark_delivery(
            event_id, success=True, now=datetime.now(timezone.utc),
        )
        rows = await self.alerts.list_open(scope_key=self.tenant_a)
        persisted = next(row for row in rows if str(row["event_id"]) == event_id)
        self.assertEqual(persisted["delivery_status"], "sent")
        self.assertEqual(persisted["delivery_attempts"], 2)

    async def test_evaluation_reports_and_human_scores_are_tenant_isolated(self):
        dataset_id = f"issue16-{uuid4().hex}"
        dataset = EvalDataset(
            dataset_id=dataset_id,
            version=1,
            cases=(EvalCase(case_id="case-1", query="question", expected_terms=("answer",)),),
        )
        await self.evaluations.save_dataset(dataset, created_by="integration")
        report = EvalReport(
            dataset_id=dataset_id,
            dataset_version=1,
            results=(EvalResult(case_id="case-1", passed=True, score=1.0),),
            pass_rate=1.0,
            average_score=1.0,
            average_recall_at_k=None,
        )
        run_a = await self.evaluations.save_report(
            report, model_key="model-a", gate_passed=True,
            gate_failures=(), tenant_id=self.tenant_a,
        )
        run_b = await self.evaluations.save_report(
            report, model_key="model-b", gate_passed=True,
            gate_failures=(), tenant_id=self.tenant_b,
        )
        reports_a = await self.evaluations.latest_reports(dataset_id, tenant_id=self.tenant_a)
        reports_b = await self.evaluations.latest_reports(dataset_id, tenant_id=self.tenant_b)
        self.assertEqual({row["eval_run_id"] for row in reports_a}, {run_a})
        self.assertEqual({row["eval_run_id"] for row in reports_b}, {run_b})
        with self.assertRaises(KeyError):
            await self.evaluations.load_report_for_tenant(
                tenant_id=self.tenant_b, eval_run_id=run_a,
            )

        with self.assertRaises(KeyError):
            await self.evaluations.save_human_score(
                tenant_id=self.tenant_b, eval_run_id=run_a, case_id="case-1",
                reviewer_id="reviewer-b", score=0.8,
            )
        await self.evaluations.save_human_score(
            tenant_id=self.tenant_a, eval_run_id=run_a, case_id="case-1",
            reviewer_id="reviewer-a", score=0.9, rationale="contains token sk-abcdefghijklmnop",
        )
        scores_a = await self.evaluations.list_human_scores(
            tenant_id=self.tenant_a, eval_run_id=run_a,
        )
        scores_b = await self.evaluations.list_human_scores(
            tenant_id=self.tenant_b, eval_run_id=run_a,
        )
        self.assertEqual(len(scores_a), 1)
        self.assertNotIn("sk-abcdefghijklmnop", scores_a[0]["rationale"])
        self.assertEqual(scores_b, [])

    async def test_prompt_rollout_policy_and_decisions_are_tenant_scoped(self):
        prompt_id = "support"
        scoped_id = tenant_prompt_key(self.tenant_a, prompt_id)
        await self.prompts.register(
            scoped_id, "Answer for {customer}", created_by="integration",
        )
        await self.prompts.create_variant(
            scoped_id, "control", 1, created_by="integration",
        )
        policy = await self.rollouts.put_policy(PromptRolloutPolicy(
            tenant_id=self.tenant_a,
            prompt_id=prompt_id,
            weights=(("control", 100.0),),
            updated_by="integration",
        ))
        self.assertEqual(policy.revision, 1)
        self.assertIsNotNone(await self.rollouts.get_policy(self.tenant_a, prompt_id))
        self.assertIsNone(await self.rollouts.get_policy(self.tenant_b, prompt_id))

        decision = PromptRouteDecision(
            tenant_id=self.tenant_a,
            prompt_id=prompt_id,
            variant="control",
            rule="percentage",
            policy_revision=policy.revision,
            subject_hash=hashlib.sha256(b"subject-a").hexdigest(),
            decided_at_epoch_ms=1_800_000_000_000,
            policy_tenant_id=self.tenant_a,
        )
        await self.rollouts.append_decision(decision)
        self.assertEqual(
            len(await self.rollouts.list_decisions(self.tenant_a, prompt_id, limit=10)), 1,
        )
        self.assertEqual(
            await self.rollouts.list_decisions(self.tenant_b, prompt_id, limit=10), [],
        )

    async def test_prompt_version_registration_serializes_concurrent_writers_and_is_immutable(self):
        prompt_id = f"issue16-{uuid4().hex}"
        versions = await asyncio.gather(*(
            self.prompts.register(
                prompt_id, f"response {{name}} variant-{index}",
                created_by=f"writer-{index}",
            )
            for index in range(5)
        ))
        self.assertEqual(sorted(item.version for item in versions), [1, 2, 3, 4, 5])
        stored = await self.prompts.list_versions(prompt_id)
        self.assertEqual([item.version for item in stored], [1, 2, 3, 4, 5])
        async with await AsyncConnection.connect(
            DSN, autocommit=True, row_factory=dict_row,
        ) as connection:
            with self.assertRaises(Exception):
                await connection.execute(
                    "UPDATE platform_prompt_versions SET template='tampered' "
                    "WHERE prompt_id=%s AND version=1",
                    (prompt_id,),
                )
        resolved = await self.prompts.resolve(prompt_id, version=1)
        self.assertTrue(resolved.template.startswith("response {name} variant-"))


if __name__ == "__main__":
    unittest.main()
