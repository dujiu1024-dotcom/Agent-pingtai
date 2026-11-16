from __future__ import annotations

import unittest

from agent_platform.quotas.token_estimator import (
    TiktokenChatEstimator,
    TokenEstimationError,
)


class FakeEncoding:
    def encode(self, value: str, *, disallowed_special=()):
        return value.split()


class ChatTokenEstimatorTests(unittest.TestCase):
    def setUp(self):
        self.estimator = TiktokenChatEstimator(lambda _model: FakeEncoding())

    def test_counts_chat_text_and_explicit_output_allowance(self):
        estimate = self.estimator.estimate(
            model_id="test-model",
            messages=[
                {"role": "system", "content": "follow policy"},
                {"role": "user", "content": "hello world"},
            ],
            request_parameters={"max_completion_tokens": 20},
            context_window_tokens=100,
        )
        self.assertGreater(estimate.input_tokens, 0)
        self.assertEqual(estimate.output_tokens, 20)
        self.assertEqual(estimate.total_tokens, estimate.input_tokens + 20)

    def test_counts_text_parts_and_tool_metadata(self):
        estimate = self.estimator.estimate(
            model_id="test-model",
            messages=[{
                "role": "assistant",
                "content": [{"type": "text", "text": "tool request"}],
                "tool_calls": [{"id": "call-1", "function": {"name": "lookup", "arguments": "{}"}}],
            }],
            request_parameters={"max_tokens": 10},
            context_window_tokens=100,
        )
        self.assertGreater(estimate.input_tokens, 10)

    def test_rejects_multimodal_content_without_provider_estimator(self):
        with self.assertRaisesRegex(TokenEstimationError, "multimodal"):
            self.estimator.estimate(
                model_id="test-model",
                messages=[{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://example.invalid"}}]}],
                request_parameters={"max_tokens": 10},
                context_window_tokens=100,
            )

    def test_requires_explicit_output_allowance_and_enforces_window(self):
        args = {
            "model_id": "test-model",
            "messages": [{"role": "user", "content": "one two three"}],
            "context_window_tokens": 10,
        }
        with self.assertRaisesRegex(TokenEstimationError, "exactly one explicit"):
            self.estimator.estimate(request_parameters={}, **args)
        with self.assertRaisesRegex(TokenEstimationError, "exceeds model context"):
            self.estimator.estimate(request_parameters={"max_tokens": 9}, **args)

    def test_unknown_model_tokenizer_fails_closed(self):
        def missing(_model):
            raise TokenEstimationError("no tokenizer mapping is available")
        estimator = TiktokenChatEstimator(missing)
        with self.assertRaisesRegex(TokenEstimationError, "no tokenizer mapping"):
            estimator.estimate(
                model_id="unmapped",
                messages=[{"role": "user", "content": "hello"}],
                request_parameters={"max_tokens": 10},
                context_window_tokens=100,
            )


if __name__ == "__main__":
    unittest.main()
