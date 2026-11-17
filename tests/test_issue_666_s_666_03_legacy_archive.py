import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "legacy" / "v1-archive"
ARCHIVED_PATHS = [
    "agency.py",
    "alerts.py",
    "boss.py",
    "browser.py",
    "console.py",
    "conversation.py",
    "db.py",
    "feishu_bot.py",
    "feishu_commands.py",
    "gui.py",
    "intent.py",
    "listener.py",
    "llm_local.py",
    "memory.py",
    "policy.py",
    "probe.py",
    "publisher.py",
    "ratelimit.py",
    "dashboard.html",
    "run-listener.sh",
    "启动服务.command"
]


class LegacyArchiveTests(unittest.TestCase):
    def test_legacy_files_are_moved_without_losing_archive_entries(self):
        for name in ARCHIVED_PATHS:
            with self.subTest(name=name):
                self.assertTrue((ARCHIVE / name).is_file())
                self.assertFalse((ROOT / name).exists())

    def test_archive_readme_documents_rollback_and_vectors_compatibility(self):
        readme = (ARCHIVE / "README.md").read_text(encoding="utf-8")
        self.assertIn("Rollback", readme)
        self.assertIn("vectors.py", readme)
        self.assertTrue((ROOT / "vectors.py").is_file())


if __name__ == "__main__":
    unittest.main()
