import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "legacy" / "v1-archive"
LEGACY_BLOB_SHAS = {
    "agency.py": "def948c269cf60ee2c7f6f960890f3a59688f141",
    "alerts.py": "8279b96a125dd64920be6e7e1e2618af687c3e7c",
    "boss.py": "5ab1cdb160f7a0c7d7f607defd7b89f7362a484a",
    "browser.py": "fe986b35b3ca55da5dfc9d01ad402ce52b531eb6",
    "console.py": "734d99f88794b60b45aa6ec5cc74fb9f6be2ae5d",
    "conversation.py": "7c523fdd71046d8163bc6e7c27d8c5341e71857e",
    "db.py": "8644710fc4b16be9f9c223b43f2f5d84c67851c9",
    "feishu_bot.py": "f658e0a2a9ddf62d6975c7a9b294f24b4c386fd6",
    "feishu_commands.py": "b56d7d7fc1e814b3b9f46e6c6ddee7003921a2b1",
    "gui.py": "113c435111e01a6e4ef9e52d1a62bd9648a95cd1",
    "intent.py": "d1138e9b767c19cef9df5693dc963e8dd9ed2e8b",
    "listener.py": "cb72f8342bd16abe6b526333fa697175067e819e",
    "llm_local.py": "00d77c0b5e69341d6e8085ddfc6b239d0e6a2dec",
    "memory.py": "19e101458f05a917f1668c3378bb69ef0c257b6c",
    "policy.py": "c625d466c8dfdc8c91ff9ba5c2d226610e3b0fb9",
    "probe.py": "7c555b618942ac47088ac6c8092569caf6856c1c",
    "publisher.py": "94f8276750f5764a0f8c839aff8283c626a36837",
    "ratelimit.py": "1685f6a8cfcb2234971bfde4c4bc1d1650d527f6",
    "vectors.py": "59fb5c60a5b313897e5c430317248b8d5e0c3898",
}


def test_all_legacy_sources_are_archived_byte_for_byte():
    assert len(LEGACY_BLOB_SHAS) == 19
    for filename, expected_sha in LEGACY_BLOB_SHAS.items():
        data = (ARCHIVE / filename).read_bytes()
        header = b"blob " + str(len(data)).encode("ascii") + bytes([0])
        actual_sha = hashlib.sha1(header + data).hexdigest()
        assert actual_sha == expected_sha, f"archived source changed: {filename}"


def test_old_implementation_paths_are_removed_from_repository_root():
    for filename in LEGACY_BLOB_SHAS:
        if filename in ("agency.py", "console.py", "listener.py"):
            continue
        assert not (ROOT / filename).exists(), f"legacy source still at root: {filename}"


def test_archive_readme_documents_inventory_history_and_rollback():
    content = (ARCHIVE / "README.md").read_text(encoding="utf-8")
    assert "git log --follow" in content
    assert "Rollback" in content
    for filename in LEGACY_BLOB_SHAS:
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
