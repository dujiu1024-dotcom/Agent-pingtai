from __future__ import annotations

import unittest

from agent_platform.observability.trace_context import (
    TraceContext, TraceContextScope, current_trace_context,
    extract_trace_context, new_trace_context,
)


class TraceContextTests(unittest.TestCase):
    def test_round_trip_headers(self):
        context = new_trace_context(correlation_id="request-42")
        extracted = extract_trace_context(context.as_headers())
        self.assertEqual(extracted, context)

    def test_malformed_and_zero_ids_are_rejected(self):
        self.assertIsNone(extract_trace_context({"traceparent": "00-bad"}))
        self.assertIsNone(extract_trace_context({
            "traceparent": "00-" + "0" * 32 + "-" + "1" * 16 + "-01"
        }))
        with self.assertRaises(ValueError):
            TraceContext("0" * 32, "1" * 16)

    def test_scope_restores_previous_context(self):
        first = new_trace_context()
        second = new_trace_context()
        with TraceContextScope(first):
            self.assertEqual(current_trace_context(), first)
            with TraceContextScope(second):
                self.assertEqual(current_trace_context(), second)
            self.assertEqual(current_trace_context(), first)
        self.assertIsNone(current_trace_context())
