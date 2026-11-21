from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "legacy" / "v1-archive"
LEGACY_SOURCES = (
    "agency.py", "alerts.py", "boss.py", "browser.py", "console.py",
    "conversation.py", "db.py", "feishu_bot.py", "feishu_commands.py", "gui.py",
    "intent.py", "listener.py", "llm_local.py", "memory.py", "policy.py",
    "probe.py", "publisher.py", "ratelimit.py", "vectors.py",
)


def test_all_legacy_sources_are_archived():
    for filename in LEGACY_SOURCES:
        assert (ARCHIVE / filename).is_file(), f"legacy source missing: {filename}"


def test_old_implementation_paths_are_removed_from_repository_root():
    for filename in LEGACY_SOURCES:
        if filename in ("agency.py", "console.py", "listener.py"):
            continue
        assert not (ROOT / filename).exists(), f"legacy source still at root: {filename}"


def test_archive_readme_documents_inventory_history_and_rollback():
    content = (ARCHIVE / "README.md").read_text(encoding="utf-8")
    assert "git log --follow" in content
    assert "Rollback" in content
    for filename in LEGACY_SOURCES:
        assert f"`{filename}`" in content


def test_legacy_launchers_preserve_original_root_path_semantics():
    for filename in ("agency.py", "console.py", "listener.py"):
        source = (ROOT / filename).read_text(encoding="utf-8")
        assert "v1-archive" in source
        assert "_TARGET.read_text" in source
        assert "compile(_SOURCE, str(Path(__file__).resolve())" in source
        assert "exec(" in source


def test_legacy_3d_console_page_is_retired_and_shell_launchers_keep_stable_paths():
    assert not (ROOT / "console.html").exists()
    assert (ROOT / "run-listener.sh").is_file()
    assert (ROOT / "启动服务.command").is_file()
