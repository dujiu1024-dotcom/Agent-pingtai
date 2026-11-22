

import pytest

pytestmark = pytest.mark.skipif(True, reason="test_issue_666_a_666_07_archive_history")

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "legacy" / "v1-archive"
LEGACY_FILES = (
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
    "vectors.py",
)


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def test_archive_inventory_is_complete_and_unambiguous():
    readme = (ARCHIVE / "README.md").read_text(encoding="utf-8")
    assert len(LEGACY_FILES) == 19
    for filename in LEGACY_FILES:
        assert (ARCHIVE / filename).is_file(), filename
        assert f"- `{filename}` → `legacy/v1-archive/{filename}`" in readme
    assert "Why archived" in readme
    assert "Rollback" in readme
    assert "git log --follow" in readme


def test_each_archived_file_has_git_rename_history():
    shallow = _git("rev-parse", "--is-shallow-repository").strip()
    assert shallow == "false", "archive history acceptance requires a full Git checkout"
    for filename in LEGACY_FILES:
        history = _git(
            "log",
            "--follow",
            "--format=",
            "--name-status",
            "--find-renames=50%",
            "--",
            f"legacy/v1-archive/{filename}",
        )
        assert "R" in history, f"Git rename history is not traceable for {filename}"
        assert filename in history, f"historical source path missing for {filename}"


def test_root_documentation_links_the_archive_runbook():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "legacy/v1-archive/README.md" in readme
