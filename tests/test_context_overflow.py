from __future__ import annotations
import unittest
from agent_platform.quotas.context_overflow import ContextBundle, ContextOverflowError, ContextSection, OverflowStrategy, prepare_context


def word_counter(sections):
    return sum(len(section.content.split()) for section in sections)


class ContextOverflowPolicyTests(unittest.TestCase):
    def setUp(self):
        self.bundle = ContextBundle((
            ContextSection("goal", "goal", "ship migration"),
            ContextSection("constraints", "constraint", "keep source"),
            ContextSection("decision", "decision", "idempotent upsert"),
            ContextSection("evidence", "evidence", "checksum matches"),
            ContextSection("history", "context", "old detail one two three four", priority=1),
            ContextSection("notes", "context", "optional note five six seven", priority=5),
        ))

    def test_reject_strategy_fails_closed(self):
        with self.assertRaisesRegex(ContextOverflowError, "request rejected"):
            prepare_context(self.bundle, max_tokens=10, strategy=OverflowStrategy.REJECT, token_counter=word_counter)

    def test_human_strategy_returns_pending_without_mutating_context(self):
        result = prepare_context(self.bundle, max_tokens=10, strategy="human", token_counter=word_counter)
        self.assertTrue(result.requires_human)
        self.assertEqual(result.sections, self.bundle.sections)
        self.assertEqual(result.preserved_keys, ("goal", "constraints", "decision", "evidence"))

    def test_truncate_never_changes_critical_sections(self):
        result = prepare_context(self.bundle, max_tokens=10, strategy="truncate", token_counter=word_counter)
        self.assertEqual(tuple(s for s in result.sections if s.is_critical), self.bundle.sections[:4])
        self.assertLessEqual(result.tokens_after, 10)
        self.assertEqual(result.removed_keys, ("history", "notes"))

    def test_summarize_only_receives_optional_context(self):
        seen = []
        def summarize(sections, allowance):
            seen.extend(section.key for section in sections)
            self.assertGreater(allowance, 0)
            return "compressed notes"
        result = prepare_context(self.bundle, max_tokens=11, strategy="summarize", token_counter=word_counter, summarizer=summarize)
        self.assertEqual(seen, ["history", "notes"])
        self.assertEqual(tuple(s.key for s in result.sections[:4]), ("goal", "constraints", "decision", "evidence"))
        self.assertEqual(result.sections[-1].key, "context-summary")
        self.assertLessEqual(result.tokens_after, 11)

    def test_critical_context_over_limit_is_never_summarized_or_truncated(self):
        with self.assertRaisesRegex(ContextOverflowError, "critical"):
            prepare_context(self.bundle, max_tokens=5, strategy="truncate", token_counter=word_counter)

    def test_summarizer_output_must_fit(self):
        with self.assertRaisesRegex(ContextOverflowError, "still exceeds"):
            prepare_context(self.bundle, max_tokens=10, strategy="summarize", token_counter=word_counter,
                            summarizer=lambda _sections, _limit: "one two three four five")

    def test_duplicate_keys_and_invalid_counts_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "unique"):
            ContextBundle((self.bundle.sections[0], self.bundle.sections[0]))
        with self.assertRaisesRegex(ContextOverflowError, "invalid token count"):
            prepare_context(self.bundle, max_tokens=10, strategy="truncate", token_counter=lambda _sections: True)

    def test_under_limit_context_is_unchanged(self):
        result = prepare_context(self.bundle, max_tokens=100, strategy="summarize", token_counter=word_counter)
        self.assertEqual(result.sections, self.bundle.sections)
        self.assertEqual(result.tokens_before, result.tokens_after)


    def test_exact_budget_boundary_does_not_trigger_degradation(self):
        calls = []

        def summarize(sections, allowance):
            calls.append((sections, allowance))
            return "should not run"

        result = prepare_context(
            self.bundle,
            max_tokens=19,
            strategy="summarize",
            token_counter=word_counter,
            summarizer=summarize,
        )

        self.assertEqual(result.sections, self.bundle.sections)
        self.assertEqual(result.tokens_before, 19)
        self.assertEqual(result.tokens_after, 19)
        self.assertEqual(calls, [])

    def test_truncate_stops_as_soon_as_budget_is_satisfied(self):
        result = prepare_context(
            self.bundle,
            max_tokens=14,
            strategy="truncate",
            token_counter=word_counter,
        )

        self.assertEqual(result.removed_keys, ("history",))
        self.assertEqual(tuple(section.key for section in result.sections),
                         ("goal", "constraints", "decision", "evidence", "notes"))
        self.assertEqual(result.tokens_after, 13)
        self.assertEqual(self.bundle.sections[-2].key, "history")

    def test_truncate_uses_original_order_for_equal_priority_sections(self):
        bundle = ContextBundle((
            ContextSection("goal", "goal", "g"),
            ContextSection("constraint", "constraint", "c"),
            ContextSection("decision", "decision", "d"),
            ContextSection("evidence", "evidence", "e"),
            ContextSection("first", "context", "a b c", priority=1),
            ContextSection("second", "context", "d e f", priority=1),
        ))

        result = prepare_context(
            bundle,
            max_tokens=7,
            strategy="truncate",
            token_counter=word_counter,
        )

        self.assertEqual(result.removed_keys, ("first",))
        self.assertEqual(tuple(section.key for section in result.sections),
                         ("goal", "constraint", "decision", "evidence", "second"))
        self.assertEqual(result.tokens_after, 7)

    def test_summarize_requires_a_summarizer(self):
        with self.assertRaisesRegex(ContextOverflowError, "requires optional context and a summarizer"):
            prepare_context(
                self.bundle,
                max_tokens=11,
                strategy="summarize",
                token_counter=word_counter,
            )

    def test_empty_summary_fails_closed(self):
        with self.assertRaisesRegex(ContextOverflowError, "empty or invalid summary"):
            prepare_context(
                self.bundle,
                max_tokens=11,
                strategy="summarize",
                token_counter=word_counter,
                summarizer=lambda _sections, _limit: "  ",
            )

    def test_critical_overflow_does_not_call_summarizer(self):
        calls = []

        def summarize(sections, allowance):
            calls.append((sections, allowance))
            return "compressed"

        with self.assertRaisesRegex(ContextOverflowError, "critical"):
            prepare_context(
                self.bundle,
                max_tokens=5,
                strategy="summarize",
                token_counter=word_counter,
                summarizer=summarize,
            )

        self.assertEqual(calls, [])

    def test_token_counter_exception_propagates_without_fallback(self):
        def broken_counter(_sections):
            raise RuntimeError("tokenizer unavailable")

        with self.assertRaisesRegex(RuntimeError, "tokenizer unavailable"):
            prepare_context(
                self.bundle,
                max_tokens=10,
                strategy="truncate",
                token_counter=broken_counter,
            )


if __name__ == "__main__":
    unittest.main()
