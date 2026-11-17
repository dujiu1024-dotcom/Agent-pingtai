from __future__ import annotations

import unittest
from pathlib import Path


class RAGQueryPlaygroundUiTests(unittest.TestCase):
    def test_navigation_and_authenticated_query_controls_are_present(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "agent_platform/web/static/index.html").read_text(encoding="utf-8")
        script = (root / "agent_platform/web/static/app.js").read_text(encoding="utf-8")
        self.assertIn('data-view="rag-playground"', html)
        self.assertIn('id="rag-query-form"', script)
        self.assertIn('API+"/rag-playground/options"', script)
        self.assertIn('API+"/rag-playground/query"', script)

    def test_result_view_exposes_ranked_sources_and_citations(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "agent_platform/web/static/app.js").read_text(encoding="utf-8")
        for contract in ("selected.retrieved", "hit.score", "hit.source_url",
                         "hit.page_number", "selected.citations", "selected.abstained"):
            with self.subTest(contract=contract):
                self.assertIn(contract, script)


if __name__ == "__main__":
    unittest.main()
