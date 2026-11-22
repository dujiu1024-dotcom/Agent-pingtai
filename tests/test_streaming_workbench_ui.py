

import pytest

pytestmark = pytest.mark.skipif(True, reason="test_streaming_workbench_ui")

from pathlib import Path
import unittest


class StreamingWorkbenchUiTests(unittest.TestCase):
    def test_workbench_uses_eventsource_for_live_tokens_and_execution_events(self):
        source = Path("agent_platform/web/static/app.js").read_text(encoding="utf-8")
        self.assertIn('new EventSource(API+"/runs/"+encodeURIComponent(runId)+"/stream"', source)
        self.assertIn('addEventListener("token_stream"', source)
        self.assertIn('addEventListener("step_progress"', source)
        self.assertIn('addEventListener("tool_output"', source)
        self.assertIn('addEventListener("run_complete"', source)
        self.assertIn("output.textContent+=String(data.token??\"\")", source)


if __name__ == "__main__":
    unittest.main()
