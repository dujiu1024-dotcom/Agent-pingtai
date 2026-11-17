from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ChunkPreviewWorkbenchContractTests(unittest.TestCase):
    def test_workbench_exposes_chunk_preview_navigation(self):
        html = (ROOT / "agent_platform/web/static/index.html").read_text(encoding="utf-8")
        self.assertIn('data-view="chunks"', html)
        self.assertIn("Chunk 预览", html)

    def test_preview_uses_authorized_document_and_chunk_apis(self):
        js = (ROOT / "agent_platform/web/static/app.js").read_text(encoding="utf-8")
        self.assertIn("/knowledge/documents?limit=100", js)
        self.assertIn('/chunks"', js)
        self.assertIn('method:"PATCH"', js)
        self.assertIn("data-chunk-edit", js)

    def test_preview_renders_provenance_and_embedding_metadata(self):
        js = (ROOT / "agent_platform/web/static/app.js").read_text(encoding="utf-8")
        for field in ("token_count", "page_number", "start_offset", "end_offset",
                      "content_hash", "embedding_status"):
            self.assertIn(field, js)


if __name__ == "__main__":
    unittest.main()
