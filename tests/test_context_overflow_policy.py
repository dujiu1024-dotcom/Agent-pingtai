from __future__ import annotations

import unittest
from types import SimpleNamespace

from agent_platform.models.context_overflow import (
    ContextOverflowError,
    HumanInterventionRequired,
    prepare_context,
)


ANCHORS = {
    "goal": "produce a reconciled inventory report",
    "constraints": ["do not alter source records", "use approved warehouse scope"],
    "decisions": ["use FIFO for the pilot"],
    "evidence": ["WMS snapshot 2026-11-16", "approval ticket E-72"],
}


class LengthEstimator:
    def estimate(self, *, model_id, messages, request_parameters, context_window_tokens):
        input_tokens = sum(len(str(message.get("content", ""))) for message in messages)
        output_tokens = request_parameters["max_tokens"]
        total = input_tokens + output_tokens
        if total > context_window_tokens:
            raise ValueError("estimated prompt plus planned output exceeds model context window")
        return SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=total)


class ContextOverflowPolicyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.estimator = LengthEstimator()
        self.messages = (
            {"role": "system", "content": "Follow the platform safety rules."},
            {"role": "user", "content": "old context " * 100},
            {"role": "assistant", "content": "intermediate reasoning " * 60},
            {"role": "user", "content": "Continue with the final inventory reconciliation."},
        )

    async def test_reject_is_default_and_never_changes_messages(self):
        with self.assertRaisesRegex(ContextOverflowError, "policy=reject"):
            await prepare_context(
                estimator=self.estimator, model_id="model-a", messages=self.messages,
                request_parameters={"max_tokens": 12}, context_window_tokens=300,
            )

    async def test_truncate_preserves_system_latest_message_and_all_protected_anchors(self):
        result = await prepare_context(
            estimator=self.estimator, model_id="model-a", messages=self.messages,
            request_parameters={"max_tokens": 12}, context_window_tokens=650,
            policy="truncate", protected_context=ANCHORS,
        )
        self.assertTrue(result.changed)
        self.assertLessEqual(result.total_tokens, 650)
        serialized = "\n".join(str(message.get("content", "")) for message in result.messages)
        self.assertIn("Follow the platform safety rules.", serialized)
        self.assertIn("Continue with the final inventory reconciliation.", serialized)
        for value in ("produce a reconciled inventory report", "do not alter source records",
                      "use FIFO for the pilot", "WMS snapshot 2026-11-16"):
            self.assertIn(value, serialized)

    async def test_summarize_policy_requires_callback_and_keeps_protected_context(self):
        async def summarize(**kwargs):
            self.assertEqual(kwargs["protected_context"], ANCHORS)
            return [{"role": "user", "content": "Summary of prior discussion."}]

        result = await prepare_context(
            estimator=self.estimator, model_id="model-a", messages=self.messages,
            request_parameters={"max_tokens": 12}, context_window_tokens=650,
            policy="summarize", protected_context=ANCHORS, summarizer=summarize,
        )
        self.assertTrue(result.changed)
        self.assertLessEqual(result.total_tokens, 650)
        self.assertIn("produce a reconciled inventory report", result.messages[0]["content"])
        self.assertIn("Summary of prior discussion.", result.messages[1]["content"])

    async def test_summarizer_output_that_still_exceeds_window_fails_closed(self):
        async def summarize(**kwargs):
            return [{"role": "user", "content": "still too long " * 100}]

        with self.assertRaisesRegex(ContextOverflowError, "still exceeds"):
            await prepare_context(
                estimator=self.estimator, model_id="model-a", messages=self.messages,
                request_parameters={"max_tokens": 12}, context_window_tokens=650,
                policy="summarize", protected_context=ANCHORS, summarizer=summarize,
            )

    async def test_human_intervention_requires_handler_and_valid_approved_context(self):
        with self.assertRaises(HumanInterventionRequired):
            await prepare_context(
                estimator=self.estimator, model_id="model-a", messages=self.messages,
                request_parameters={"max_tokens": 12}, context_window_tokens=650,
                policy="human_intervention", protected_context=ANCHORS,
            )

        async def approve(**kwargs):
            self.assertGreater(kwargs["estimated_total_tokens"], kwargs["context_window_tokens"])
            return [{"role": "user", "content": "Operator-approved short context."}]

        result = await prepare_context(
            estimator=self.estimator, model_id="model-a", messages=self.messages,
            request_parameters={"max_tokens": 12}, context_window_tokens=650,
            policy="human_intervention", protected_context=ANCHORS,
            intervention_handler=approve,
        )
        self.assertTrue(result.changed)
        self.assertIn("Operator-approved short context.", result.messages[1]["content"])

    async def test_non_reject_policy_requires_all_four_protected_fields(self):
        with self.assertRaisesRegex(ContextOverflowError, "missing evidence"):
            await prepare_context(
                estimator=self.estimator, model_id="model-a", messages=self.messages,
                request_parameters={"max_tokens": 12}, context_window_tokens=650,
                policy="truncate", protected_context={
                    "goal": "goal", "constraints": [], "decisions": [],
                },
            )

    async def test_unknown_tokenizer_failure_is_not_converted_to_truncation(self):
        class UnknownTokenizer:
            def estimate(self, **kwargs):
                raise ValueError("no tokenizer mapping is available")

        with self.assertRaisesRegex(ValueError, "no tokenizer mapping"):
            await prepare_context(
                estimator=UnknownTokenizer(), model_id="unknown", messages=self.messages,
                request_parameters={"max_tokens": 12}, context_window_tokens=650,
                policy="truncate", protected_context=ANCHORS,
            )


if __name__ == "__main__":
    unittest.main()
