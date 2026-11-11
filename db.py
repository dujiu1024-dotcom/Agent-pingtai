#!/usr/bin/env python3
"""db.py — 底座表结构与迁移

单一事实源：agency/state/agency.db。纯标准库，无 ORM。
枚举只在本文件定义一处，代码与文档同源（见 08 §1）。

用法：
  python3 db.py init        建库/补表
  python3 db.py status      看版本与行数
  python3 db.py reset --yes 删库重建（仅开发用）
"""

import argparse
import datetime as dt
import hashlib
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("AGENCY_DB") or ROOT / "agency" / "state" / "agency.db")

# ---------- 枚举（唯一定义处） ----------

TASK_STATUS = ("todo", "doing", "blocked", "review", "done", "closed")
SOURCE_LEVEL = ("S1", "S2", "S3", "S4")
AUTH_LEVEL = ("A0", "A1", "A2", "A3")
CLASSIFICATION = ("L1", "L2", "L3", "L4")
EVENT_TYPE = (
    "task_created", "task_due_soon", "task_overdue", "task_blocked",
    "milestone_hit", "gate_blocked", "sync_conflict",
)
SYNC_STATUS = ("pending", "synced", "sync_lost", "conflict")


def _in(values):
    return "(" + ", ".join("'%s'" % v for v in values) + ")"


# ---------- 迁移 ----------

MIGRATIONS = [
    (1, "初始表结构", [
        """
        CREATE TABLE projects (
            id          TEXT PRIMARY KEY,
            code        TEXT NOT NULL UNIQUE,
            name        TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'active',
            created_at  TEXT NOT NULL,
            confirmed_at TEXT,
            confirm_scope TEXT
        )
        """,
        f"""
        CREATE TABLE tasks (
            id            TEXT PRIMARY KEY,
            project_id    TEXT REFERENCES projects(id),
            title         TEXT NOT NULL,
            kind          TEXT NOT NULL DEFAULT 'plan',
            owner         TEXT NOT NULL,
            status        TEXT NOT NULL DEFAULT 'todo'
                          CHECK (status IN {_in(TASK_STATUS)}),
            start_at      TEXT,
            due_at        TEXT,
            done_when     TEXT NOT NULL,
            evidence      TEXT NOT NULL DEFAULT '',
            blocked_reason TEXT,
            hours         REAL,
            dep           TEXT,
            deliverable   TEXT,
            milestone     TEXT,
            content_hash  TEXT,
            created_at    TEXT NOT NULL,
            closed_at     TEXT
        )
        """,
        "CREATE INDEX idx_tasks_due ON tasks(status, due_at)",
        f"""
        CREATE TABLE events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            type        TEXT NOT NULL CHECK (type IN {_in(EVENT_TYPE)}),
            task_id     TEXT REFERENCES tasks(id),
            payload     TEXT NOT NULL DEFAULT '{{}}',
            dedup_key   TEXT NOT NULL UNIQUE,
            created_at  TEXT NOT NULL,
            delivered_at TEXT
        )
        """,
        "CREATE INDEX idx_events_pending ON events(delivered_at, created_at)",
        f"""
        CREATE TABLE facts (
            id           TEXT PRIMARY KEY,
            project_id   TEXT REFERENCES projects(id),
            content      TEXT NOT NULL,
            source_level TEXT NOT NULL CHECK (source_level IN {_in(SOURCE_LEVEL)}),
            source_ref   TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            state        TEXT NOT NULL DEFAULT 'active',
            expires_at   TEXT,
            created_at   TEXT NOT NULL
        )
        """,
        "CREATE UNIQUE INDEX idx_facts_hash ON facts(project_id, content_hash)",
        """
        CREATE TABLE reminders (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id      TEXT NOT NULL REFERENCES tasks(id),
            kind         TEXT NOT NULL,
            scheduled_at TEXT NOT NULL,
            sent_at      TEXT,
            count        INTEGER NOT NULL DEFAULT 0
        )
        """,
        f"""
        CREATE TABLE feishu_tasks (
            local_task_id  TEXT PRIMARY KEY REFERENCES tasks(id),
            feishu_guid    TEXT NOT NULL,
            identity       TEXT NOT NULL DEFAULT 'bot' CHECK (identity IN ('bot', 'user')),
            sync_status    TEXT NOT NULL DEFAULT 'pending'
                           CHECK (sync_status IN {_in(SYNC_STATUS)}),
            last_synced_at TEXT,
            remote_done    INTEGER NOT NULL DEFAULT 0
        )
        """,
        """
        CREATE TABLE docs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            local_path   TEXT NOT NULL,
            node_token   TEXT,
            content_hash TEXT NOT NULL,
            classification TEXT NOT NULL DEFAULT 'L1'
                         CHECK (classification IN ('L1', 'L2', 'L3', 'L4')),
            synced_at    TEXT,
            state        TEXT NOT NULL DEFAULT 'local'
        )
        """,
        "CREATE UNIQUE INDEX idx_docs_path ON docs(local_path)",
        """
        CREATE TABLE agent_runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            role        TEXT NOT NULL,
            task_id     TEXT REFERENCES tasks(id),
            model       TEXT NOT NULL,
            prompt_hash TEXT NOT NULL,
            output_path TEXT,
            tokens_in   INTEGER NOT NULL DEFAULT 0,
            tokens_out  INTEGER NOT NULL DEFAULT 0,
            ok          INTEGER NOT NULL DEFAULT 1,
            error       TEXT,
            created_at  TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE dead_letter (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id   INTEGER REFERENCES events(id),
            role       TEXT,
            error      TEXT NOT NULL,
            attempts   INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            resolved_at TEXT
        )
        """,
        """
        CREATE TABLE config_kv (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """,
    ]),
    (2, "工作记忆与向量记忆", [
        """
        CREATE TABLE wm_session (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id         TEXT NOT NULL REFERENCES tasks(id),
            role            TEXT NOT NULL,
            turn            INTEGER NOT NULL DEFAULT 0,
            goal            TEXT NOT NULL DEFAULT '',
            constraints     TEXT NOT NULL DEFAULT '[]',
            decisions       TEXT NOT NULL DEFAULT '[]',
            open_questions  TEXT NOT NULL DEFAULT '[]',
            artifacts       TEXT NOT NULL DEFAULT '[]',
            digest          TEXT NOT NULL DEFAULT '',
            token_used      INTEGER NOT NULL DEFAULT 0,
            compacted_from  INTEGER,
            updated_at      TEXT NOT NULL
        )
        """,
        "CREATE UNIQUE INDEX idx_wm_task_role ON wm_session(task_id, role)",
        """
        CREATE TABLE embedding_models (
            model     TEXT NOT NULL,
            dim       INTEGER NOT NULL,
            normalize INTEGER NOT NULL DEFAULT 1,
            provider  TEXT NOT NULL,
            state     TEXT NOT NULL DEFAULT 'active',
            added_at  TEXT NOT NULL,
            PRIMARY KEY (model, dim)
        )
        """,
        """
        CREATE TABLE embeddings (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id     TEXT REFERENCES projects(id),
            source_type    TEXT NOT NULL CHECK (source_type IN ('fact', 'doc', 'case')),
            source_id      TEXT NOT NULL,
            source_version TEXT,
            chunk_no       INTEGER NOT NULL DEFAULT 0,
            text           TEXT NOT NULL,
            content_hash   TEXT NOT NULL,
            model          TEXT NOT NULL,
            dim            INTEGER NOT NULL,
            vector         BLOB NOT NULL,
            state          TEXT NOT NULL DEFAULT 'active',
            created_at     TEXT NOT NULL
        )
        """,
        "CREATE UNIQUE INDEX idx_emb_dedup ON embeddings(source_type, source_id, chunk_no, model)",
        "CREATE INDEX idx_emb_project ON embeddings(project_id, state)",
    ]),
]


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def content_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def connect(path=None):
    p = Path(path or DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA synchronous = NORMAL")
    return con


def current_version(con):
    row = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    if not row:
        return 0
    return con.execute("SELECT COALESCE(MAX(version), 0) AS v FROM schema_version").fetchone()["v"]


def migrate(con, verbose=True):
    if not con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone():
        con.execute(
            "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, note TEXT, applied_at TEXT NOT NULL)"
        )
    version = current_version(con)
    applied = 0
    for ver, note, statements in MIGRATIONS:
        if ver <= version:
            continue
        with con:
            for sql in statements:
                con.execute(sql)
            con.execute(
                "INSERT INTO schema_version(version, note, applied_at) VALUES (?, ?, ?)",
                (ver, note, now()),
            )
        applied += 1
        if verbose:
            print(f"[迁移] v{ver} {note} 完成")
    # 内置嵌入模型注册（已实测）
    with con:
        con.execute(
            "INSERT OR IGNORE INTO embedding_models(model, dim, normalize, provider, state, added_at)"
            " VALUES ('bge-m3', 1024, 1, 'ollama', 'active', ?)",
            (now(),),
        )
    return applied


TABLES = [
    "projects", "tasks", "events", "facts", "reminders", "feishu_tasks",
    "docs", "agent_runs", "dead_letter", "config_kv",
    "wm_session", "embedding_models", "embeddings",
]


def cmd_init(_):
    con = connect()
    n = migrate(con)
    print(f"数据库：{DB_PATH}")
    print("当前版本：v%d" % current_version(con))
    print("本次迁移：%d 个" % n)
    con.close()


def cmd_status(_):
    if not DB_PATH.exists():
        print("数据库不存在，先执行：python3 db.py init")
        return 1
    con = connect()
    print(f"数据库：{DB_PATH}  版本 v{current_version(con)}")
    for t in TABLES:
        try:
            n = con.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
            print(f"  {t:<18} {n}")
        except sqlite3.OperationalError:
            print(f"  {t:<18} 缺失")
    con.close()
    return 0


def cmd_reset(args):
    if not args.yes:
        sys.exit("危险操作：会删除全部数据。确认请加 --yes")
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(DB_PATH) + suffix)
        if p.exists():
            p.unlink()
    cmd_init(args)
    return 0


def main():
    p = argparse.ArgumentParser(description="底座数据库")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    sub.add_parser("status")
    pr = sub.add_parser("reset")
    pr.add_argument("--yes", action="store_true")
    args = p.parse_args()
    if args.cmd == "init":
        sys.exit(cmd_init(args))
    if args.cmd == "status":
        sys.exit(cmd_status(args))
    if args.cmd == "reset":
        sys.exit(cmd_reset(args))


if __name__ == "__main__":
    main()
