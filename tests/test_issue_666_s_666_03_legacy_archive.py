from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "legacy" / "v1-archive"
LEGACY_MODULES = (
    "agency.py", "alerts.py", "boss.py", "browser.py", "console.py",
    "conversation.py", "db.py", "feishu_bot.py", "feishu_commands.py",
    "gui.py", "intent.py", "listener.py", "llm_local.py", "memory.py",
    "policy.py", "probe.py", "publisher.py", "ratelimit.py", "vectors.py",
)
REMOVED_ROOT_MODULES = (
    "alerts.py", "boss.py", "browser.py", "conversation.py", "db.py",
    "feishu_bot.py", "feishu_commands.py", "gui.py", "intent.py",
    "llm_local.py", "memory.py", "policy.py", "probe.py",
    "publisher.py", "ratelimit.py", "vectors.py",
)


def test_all_legacy_sources_are_archived_without_root_implementation_copies():
    for filename in LEGACY_MODULES:
        assert (ARCHIVE / filename).is_file(), f"missing archived source: {filename}"
    for filename in REMOVED_ROOT_MODULES:
        assert not (ROOT / filename).exists(), f"legacy source still at root: {filename}"


def test_archive_readme_documents_inventory_history_and_rollback():
    content = (ARCHIVE / "README.md").read_text(encoding="utf-8")
    assert "git log --follow" in content
    assert "Rollback" in content
    for filename in LEGACY_MODULES:
        assert f"`{filename}`" in content


def test_legacy_launchers_preserve_original_root_path_semantics():
    for filename in ("agency.py", "console.py", "listener.py"):
        source = (ROOT / filename).read_text(encoding="utf-8")
        assert "v1-archive" in source
        assert "_TARGET.read_text" in source
        assert "compile(_SOURCE, str(Path(__file__).resolve())" in source
        assert "exec(" in source


def test_legacy_console_fallback_and_shell_launchers_keep_stable_paths():
    assert (ROOT / "console.html").is_file()
    assert (ROOT / "run-listener.sh").is_file()
    assert (ROOT / "启动服务.command").is_file()
