from __future__ import annotations

import unittest

from agent_platform.observability.evaluation_reports import (
    build_variant_comparison, render_variant_report_html, render_variant_report_pdf,
)


def sample_run():
    return {
        "eval_run_id": "eval-1", "dataset_id": "support", "dataset_version": 3,
        "variants": [
            {"model_variant": "model-b", "case_count": 2, "passed_count": 2, "pass_rate": 1.0, "average_score": 0.9, "input_tokens": 20, "output_tokens": 10, "total_tokens": 30, "cost_usd": "0.002", "average_latency_ms": 80.0, "p50_latency_ms": 80.0, "p95_latency_ms": 100.0},
            {"model_variant": "model-a", "case_count": 2, "passed_count": 1, "pass_rate": 0.5, "average_score": 0.8, "input_tokens": 18, "output_tokens": 8, "total_tokens": 26, "cost_usd": None, "average_latency_ms": 40.0, "p50_latency_ms": 40.0, "p95_latency_ms": 50.0},
            {"model_variant": "model-c", "case_count": 2, "passed_count": 2, "pass_rate": 1.0, "average_score": 0.9, "input_tokens": 22, "output_tokens": 9, "total_tokens": 31, "cost_usd": "0.001", "average_latency_ms": 90.0, "p50_latency_ms": 90.0, "p95_latency_ms": 110.0},
        ],
    }


class VariantReportTests(unittest.TestCase):
    def test_matrix_recommendation_is_deterministic_and_explainable(self):
        report = build_variant_comparison(sample_run())
        self.assertEqual([row["model_variant"] for row in report["matrix"]], ["model-c", "model-b", "model-a"])
        self.assertEqual(report["recommendation"]["model_variant"], "model-c")
        self.assertIn("成本", " ".join(report["recommendation"]["reason"]))
        self.assertIsNone(report["matrix"][2]["cost_usd"])

    def test_html_escapes_untrusted_variant_names(self):
        run = sample_run()
        run["variants"][0]["model_variant"] = "<script>alert(1)</script>"
        rendered = render_variant_report_html(build_variant_comparison(run))
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)

    def test_pdf_has_valid_header_xref_and_eof(self):
        pdf = render_variant_report_pdf(build_variant_comparison(sample_run()))
        self.assertTrue(pdf.startswith(b"%PDF-1.4"))
        self.assertIn(b"xref\n", pdf)
        self.assertTrue(pdf.rstrip().endswith(b"%%EOF"))

    def test_invalid_or_duplicate_variant_metrics_fail_closed(self):
        run = sample_run()
        run["variants"][0]["pass_rate"] = 1.5
        with self.assertRaisesRegex(ValueError, "pass_rate"):
            build_variant_comparison(run)
        run = sample_run()
        run["variants"][1]["model_variant"] = "model-b"
        with self.assertRaisesRegex(ValueError, "duplicate"):
            build_variant_comparison(run)


if __name__ == "__main__":
    unittest.main()
