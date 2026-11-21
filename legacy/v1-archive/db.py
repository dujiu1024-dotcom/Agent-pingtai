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
import time
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("AGENCY_DB") or ROOT / "agency" / "state" / "agency.db")


def canonical_db(env):
    """一个环境唯一允许的库路径；不存在第二套默认值。"""
    return (ROOT / "agency" / ("dev-state" if env == "dev" else "state")
            / "agency.db").resolve()


def require_env():
    """启动闸门：必须显式声明环境，且库路径与环境一致。

    - 不设 AGENCY_ENV → 拒绝，绝不默认 prod；
    - AGENCY_ENV=dev 却指向正式库，或 prod 指向副本库 → 拒绝；
    - 仅测试/演练可用 AGENCY_ALLOW_CUSTOM_DB=1 放行自定义库。
    """
    env = os.environ.get("AGENCY_ENV", "").strip().lower()
    if env not in ("dev", "prod"):
        raise SystemExit(
            "拒绝启动：未显式设置 AGENCY_ENV。\n"
            "  dev  ：export AGENCY_ENV=dev  （只用 agency/dev-state/agency.db）\n"
            "  prod ：export AGENCY_ENV=prod （会读写正式库 agency/state/agency.db）\n"
            "  launchd 里检查 EnvironmentVariables.AGENCY_ENV。")
    expected = canonical_db(env)
    current = Path(os.environ.get("AGENCY_DB") or expected).resolve()
    if current != expected and os.environ.get("AGENCY_ALLOW_CUSTOM_DB") != "1":
        raise SystemExit(
            "拒绝启动：AGENCY_ENV=%s 只允许 %s，当前 AGENCY_DB=%s。\n"
            "  测试/演练确实要用自定义库，再设 AGENCY_ALLOW_CUSTOM_DB=1。"
            % (env, expected, current))
    os.environ.setdefault("AGENCY_DB", str(expected))
    return env, current

# ---------- 枚举（唯一定义处） ----------

TASK_STATUS = ("todo", "doing", "blocked", "review", "done", "canceled")
SOURCE_LEVEL = ("S1", "S2", "S3", "S4")
AUTH_LEVEL = ("A0", "A1", "A2", "A3")
CLASSIFICATION = ("L1", "L2", "L3", "L4")
EVENT_TYPE = (
    "project_created", "project_confirmed", "tasks_created",
    "task_created", "task_closed", "task_canceled", "task_reopened",
    "task_due_soon", "task_overdue", "task_blocked",
    "milestone_hit", "milestone_deviation",
    "gate_confirmed", "gate_revoked", "gate_blocked",
    "agent_run", "audit_flag", "check", "sync_conflict",
    "doc_ready_review", "contract_signed", "invoice_overdue",
    "dependency_overdue",
    # HR 专属（T-0032 / 2026-11-19）
    "agent_circuit_break", "agent_low_output",
    "agent_role_overlap", "agent_subscribe_flood",
    "quarterly_review",
)
SYNC_STATUS = ("pending", "synced", "sync_lost", "conflict")

# 角色注册表：与 agents/<role>/ROLE.md 一一对应，新增角色只需加目录 + 在此登记
ROLE = (
    "chief-of-staff", "auditor", "pm", "planner", "secretary",
    "coach", "scout", "content", "finance", "producer",
)
TASK_KIND = ("general", "code", "debt")
GATE_TYPE = ("start", "finance")
# 财务台账状态（T-0017）
QUOTE_STATUS = ("draft", "sent", "won", "lost")
INVOICE_STATUS = ("issued", "paid", "void")
COST_CATEGORY = ("labor", "cloud", "tool", "travel", "other")

# 知识图谱占位（T-0020 启用，空表已在迁移 v5 建好）
ENTITY_TYPE = ("project", "task", "role", "doc", "client", "fact")
RELATION_TYPE = ("has_task", "owned_by", "depends_on", "mentions", "belongs_to", "supersedes")
ENTITY_ID_PREFIX = {
    "project": "proj", "task": "task", "role": "role",
    "doc": "doc", "client": "client", "fact": "fact",
}


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
    (3, "闸门确认人与事件类型对齐 CLI", [
        "ALTER TABLE projects ADD COLUMN confirm_by TEXT",
        "ALTER TABLE projects ADD COLUMN gate_revoked_at TEXT",
        f"""
        CREATE TABLE events_v3 (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            type        TEXT NOT NULL CHECK (type IN {_in(EVENT_TYPE)}),
            task_id     TEXT REFERENCES tasks(id),
            payload     TEXT NOT NULL DEFAULT '{{}}',
            dedup_key   TEXT NOT NULL UNIQUE,
            created_at  TEXT NOT NULL,
            delivered_at TEXT
        )
        """,
        """
        INSERT INTO events_v3(id, type, task_id, payload, dedup_key, created_at, delivered_at)
        SELECT id, type, task_id, payload, dedup_key, created_at, delivered_at FROM events
        """,
        "DROP TABLE events",
        "ALTER TABLE events_v3 RENAME TO events",
        "CREATE INDEX idx_events_pending ON events(delivered_at, created_at)",
    ]),
    (4, "任务更新时间", [
        "ALTER TABLE tasks ADD COLUMN updated_at TEXT",
    ]),
    (5, "调度与财务骨骼：一次建齐空表", [
        # 事件表重建：CHECK 约束随 EVENT_TYPE 扩展
        f"""
        CREATE TABLE events_v5 (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            type        TEXT NOT NULL CHECK (type IN {_in(EVENT_TYPE)}),
            task_id     TEXT REFERENCES tasks(id),
            payload     TEXT NOT NULL DEFAULT '{{}}',
            dedup_key   TEXT NOT NULL UNIQUE,
            created_at  TEXT NOT NULL,
            delivered_at TEXT
        )
        """,
        """
        INSERT INTO events_v5(id, type, task_id, payload, dedup_key, created_at, delivered_at)
        SELECT id, type, task_id, payload, dedup_key, created_at, delivered_at FROM events
        """,
        "DROP TABLE events",
        "ALTER TABLE events_v5 RENAME TO events",
        "CREATE INDEX idx_events_pending ON events(delivered_at, created_at)",
        # 财务（阶段 B 只填逻辑）
        """
        CREATE TABLE quotes (
            id           TEXT PRIMARY KEY,
            project_id   TEXT REFERENCES projects(id),
            amount       REAL NOT NULL,
            cost         REAL,
            margin       REAL,
            terms        TEXT,
            valid_until  TEXT,
            status       TEXT NOT NULL DEFAULT 'draft',
            content_hash TEXT,
            created_at   TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE contracts (
            id            TEXT PRIMARY KEY,
            project_id    TEXT REFERENCES projects(id),
            quote_id      TEXT REFERENCES quotes(id),
            amount        REAL,
            signed_at     TEXT,
            payment_terms TEXT,
            file_path     TEXT,
            content_hash  TEXT,
            created_at    TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE invoices (
            id          TEXT PRIMARY KEY,
            contract_id TEXT REFERENCES contracts(id),
            no          TEXT UNIQUE,
            amount      REAL NOT NULL,
            issued_at   TEXT,
            due_at      TEXT,
            status      TEXT NOT NULL DEFAULT 'issued',
            created_at  TEXT NOT NULL
        )
        """,
        "CREATE INDEX idx_invoices_due ON invoices(status, due_at)",
        """
        CREATE TABLE payments (
            id         TEXT PRIMARY KEY,
            invoice_id TEXT REFERENCES invoices(id),
            amount     REAL NOT NULL,
            paid_at    TEXT,
            method     TEXT,
            note       TEXT,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE costs (
            id          TEXT PRIMARY KEY,
            project_id  TEXT REFERENCES projects(id),
            category    TEXT,
            amount      REAL,
            hours       REAL,
            occurred_at TEXT,
            note        TEXT,
            created_at  TEXT NOT NULL
        )
        """,
        # 知识图谱占位（T-0020 启用）
        f"""
        CREATE TABLE entities (
            id           TEXT PRIMARY KEY,
            type         TEXT NOT NULL CHECK (type IN {_in(ENTITY_TYPE)}),
            name         TEXT NOT NULL,
            project_id   TEXT REFERENCES projects(id),
            source_ref   TEXT,
            content_hash TEXT,
            created_at   TEXT NOT NULL
        )
        """,
        f"""
        CREATE TABLE relations (
            src_id       TEXT NOT NULL,
            rel_type     TEXT NOT NULL CHECK (rel_type IN {_in(RELATION_TYPE)}),
            dst_id       TEXT NOT NULL,
            content_hash TEXT,
            created_at   TEXT NOT NULL,
            PRIMARY KEY (src_id, rel_type, dst_id)
        )
        """,
        # 事件订阅（阶段 D 启用；默认关闭，需显式开启）
        f"""
        CREATE TABLE subscriptions (
            event_type TEXT NOT NULL CHECK (event_type IN {_in(EVENT_TYPE)}),
            target     TEXT NOT NULL,
            action     TEXT NOT NULL,
            enabled    INTEGER NOT NULL DEFAULT 0,
            budget     INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            PRIMARY KEY (event_type, target, action)
        )
        """,
    ]),
    (6, "任务状态机：done/canceled 与重开审计", [
        # SQLite 无法修改 CHECK 约束，只能重建表；closed 统一迁移为 done，
        # 作废任务在数据修订时单独改为 canceled（见 2026-11-12 存量修正）。
        """
        CREATE TABLE tasks_v6 (
            id            TEXT PRIMARY KEY,
            project_id    TEXT REFERENCES projects(id),
            title         TEXT NOT NULL,
            kind          TEXT NOT NULL DEFAULT 'general',
            owner         TEXT NOT NULL,
            status        TEXT NOT NULL DEFAULT 'todo'
                          CHECK (status IN ('todo', 'doing', 'blocked', 'review', 'done', 'canceled')),
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
            closed_at     TEXT,
            updated_at    TEXT,
            reopen_count  INTEGER NOT NULL DEFAULT 0,
            reopen_reason TEXT
        )
        """,
        """
        INSERT INTO tasks_v6(
            id, project_id, title, kind, owner, status, start_at, due_at,
            done_when, evidence, blocked_reason, hours, dep, deliverable,
            milestone, content_hash, created_at, closed_at, updated_at,
            reopen_count, reopen_reason)
        SELECT id, project_id, title, kind, owner,
               CASE status WHEN 'closed' THEN 'done' ELSE status END,
               start_at, due_at, done_when, evidence, blocked_reason, hours, dep,
               deliverable, milestone, content_hash, created_at, closed_at,
               updated_at, 0, NULL
        FROM tasks
        """,
        "DROP TABLE tasks",
        "ALTER TABLE tasks_v6 RENAME TO tasks",
        "CREATE INDEX idx_tasks_due ON tasks(status, due_at)",
        "CREATE INDEX idx_tasks_hash ON tasks(content_hash)",
    ]),
    (7, "agent_runs 聚合索引", [
        "CREATE INDEX IF NOT EXISTS idx_agent_runs_created ON agent_runs(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_agent_runs_role ON agent_runs(role, created_at)",
    ]),
    (8, "事件表约束补齐作废/重开类型", [
        # 旧库 v5 建表时 EVENT_TYPE 尚未含 task_canceled/task_reopened，
        # 导致这两类事件被 CHECK 静默丢弃；按当前 EVENT_TYPE 重建。
        f"""
        CREATE TABLE events_v8 (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            type        TEXT NOT NULL CHECK (type IN {_in(EVENT_TYPE)}),
            task_id     TEXT REFERENCES tasks(id),
            payload     TEXT NOT NULL DEFAULT '{{}}',
            dedup_key   TEXT NOT NULL UNIQUE,
            created_at  TEXT NOT NULL,
            delivered_at TEXT
        )
        """,
        """
        INSERT INTO events_v8(id, type, task_id, payload, dedup_key, created_at, delivered_at)
        SELECT id, type, task_id, payload, dedup_key, created_at, delivered_at FROM events
        """,
        "DROP TABLE events",
        "ALTER TABLE events_v8 RENAME TO events",
        "CREATE INDEX idx_events_pending ON events(delivered_at, created_at)",
    ]),
    (9, "依赖清单：外部缺料台账", [
        # 新增 dependency_overdue 事件类型，事件表 CHECK 需同步重建
        f"""
        CREATE TABLE events_v9 (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            type        TEXT NOT NULL CHECK (type IN {_in(EVENT_TYPE)}),
            task_id     TEXT REFERENCES tasks(id),
            payload     TEXT NOT NULL DEFAULT '{{}}',
            dedup_key   TEXT NOT NULL UNIQUE,
            created_at  TEXT NOT NULL,
            delivered_at TEXT
        )
        """,
        """
        INSERT INTO events_v9(id, type, task_id, payload, dedup_key, created_at, delivered_at)
        SELECT id, type, task_id, payload, dedup_key, created_at, delivered_at FROM events
        """,
        "DROP TABLE events",
        "ALTER TABLE events_v9 RENAME TO events",
        "CREATE INDEX idx_events_pending ON events(delivered_at, created_at)",
        # 外部依赖（缺料）清单：只登记真实台账里被卡住的事项
        """
        CREATE TABLE dependencies (
            id          TEXT PRIMARY KEY,
            need        TEXT NOT NULL,
            impact      TEXT NOT NULL DEFAULT '',
            assignee    TEXT NOT NULL DEFAULT '',
            due_at      TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'provided', 'overdue')),
            provided_at TEXT,
            note        TEXT NOT NULL DEFAULT '',
            source      TEXT NOT NULL DEFAULT '',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )
        """,
        "CREATE INDEX idx_dependencies_due ON dependencies(status, due_at)",
    ]),
    (10, "订阅表事件类型补齐依赖逾期", [
        f"""
        CREATE TABLE subscriptions_v10 (
            event_type TEXT NOT NULL CHECK (event_type IN {_in(EVENT_TYPE)}),
            target     TEXT NOT NULL,
            action     TEXT NOT NULL,
            enabled    INTEGER NOT NULL DEFAULT 0,
            budget     INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            PRIMARY KEY (event_type, target, action)
        )
        """,
        """
        INSERT INTO subscriptions_v10(event_type, target, action, enabled, budget, created_at)
        SELECT event_type, target, action, enabled, budget, created_at FROM subscriptions
        """,
        "DROP TABLE subscriptions",
        "ALTER TABLE subscriptions_v10 RENAME TO subscriptions",
    ]),
    (11, "依赖状态增加待确认，解除未确认项催办", [
        # 未经创始人确认的依赖不得进入逾期提醒：新增 unconfirmed 状态
        """
        CREATE TABLE dependencies_v11 (
            id          TEXT PRIMARY KEY,
            need        TEXT NOT NULL,
            impact      TEXT NOT NULL DEFAULT '',
            assignee    TEXT NOT NULL DEFAULT '',
            due_at      TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'unconfirmed'
                        CHECK (status IN ('unconfirmed', 'pending', 'provided', 'overdue')),
            provided_at TEXT,
            note        TEXT NOT NULL DEFAULT '',
            source      TEXT NOT NULL DEFAULT '',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )
        """,
        """
        INSERT INTO dependencies_v11(id, need, impact, assignee, due_at, status,
                                     provided_at, note, source, created_at, updated_at)
        SELECT id, need, impact, assignee, due_at, status,
               provided_at, note, source, created_at, updated_at FROM dependencies
        """,
        "DROP TABLE dependencies",
        "ALTER TABLE dependencies_v11 RENAME TO dependencies",
        "CREATE INDEX idx_dependencies_due ON dependencies(status, due_at)",
    ]),
    (12, "按人的多轮会话上下文", [
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            open_id    TEXT NOT NULL,
            role       TEXT NOT NULL,
            text       TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_conversations_open ON conversations(open_id, id)",
    ]),
    (13, "客户表与报价/合同客户外键", [
        """
        CREATE TABLE IF NOT EXISTS clients (
            id         TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            contact    TEXT,
            phone      TEXT,
            email      TEXT,
            note       TEXT,
            created_at TEXT NOT NULL
        )
        """,
        "ALTER TABLE quotes ADD COLUMN client_id TEXT REFERENCES clients(id)",
        "ALTER TABLE contracts ADD COLUMN client_id TEXT REFERENCES clients(id)",
    ]),
    (14, "交付物访问令牌", [
        """
        CREATE TABLE IF NOT EXISTS deliveries (
            token      TEXT PRIMARY KEY,
            path       TEXT NOT NULL,
            project_id TEXT,
            title      TEXT,
            created_at TEXT NOT NULL,
            expires_at TEXT,
            revoked_at TEXT
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_deliveries_exp ON deliveries(expires_at)",
    ]),
    (15, "内容日历", [
        """
        CREATE TABLE IF NOT EXISTS content_plan (
            id           TEXT PRIMARY KEY,
            channel      TEXT NOT NULL,
            topic        TEXT,
            title        TEXT NOT NULL,
            planned_at   TEXT,
            status       TEXT NOT NULL DEFAULT 'planned',
            published_at TEXT,
            views        INTEGER NOT NULL DEFAULT 0,
            likes        INTEGER NOT NULL DEFAULT 0,
            comments     INTEGER NOT NULL DEFAULT 0,
            dms          INTEGER NOT NULL DEFAULT 0,
            note         TEXT,
            created_at   TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_content_plan ON content_plan(channel, planned_at)",
    ]),
    (16, "多用户与权限", [
        """
        CREATE TABLE IF NOT EXISTS users (
            open_id       TEXT PRIMARY KEY,
            name          TEXT NOT NULL,
            role          TEXT NOT NULL,
            projects      TEXT NOT NULL DEFAULT '',
            console_token TEXT UNIQUE,
            created_at    TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_users_token ON users(console_token)",
    ]),
    (17, "商务单据链路：合同金额差异原因", [
        # 合同金额与报价不一致时必须显式记录差异原因，否则视为脱链。
        "ALTER TABLE contracts ADD COLUMN diff_reason TEXT",
    ]),
    (18, "失败运行可排队重试：死信记录任务号", [
        # 只有角色+任务号都齐全时，tick 才能自动把失败运行放回队列重跑。
        "ALTER TABLE dead_letter ADD COLUMN task_id TEXT",
    ]),
    (19, "出站消息去重：事件 + 类型只发一次", [
        "CREATE TABLE IF NOT EXISTS outbound_sent("
        " event_id TEXT NOT NULL, kind TEXT NOT NULL, chat_id TEXT,"
        " message_id TEXT, sent_at REAL NOT NULL,"
        " PRIMARY KEY(event_id, kind))",
    ]),
    (20, "重试队列保留原始指令", [
        # 429/额度等失败自动重跑时必须带上原 note，否则重跑的不是同一件事。
        "ALTER TABLE dead_letter ADD COLUMN note TEXT",
    ]),
    (21, "重试队列记录最近一次尝试时间", [
        # 退避要按上次尝试时间起算，否则重跑一次后无法判断间隔。
        "ALTER TABLE dead_letter ADD COLUMN last_try_at TEXT",
    ]),
    (22, "重试成功后把结果发回原会话", [
        # 额度恢复自动重跑后，结果要自己回到创始人会话，不用人再问一遍。
        "ALTER TABLE dead_letter ADD COLUMN chat_id TEXT",
    ]),
    (23, "定时任务执行台账", [
        # 到点动作必须留痕：每个内部调度每跑一轮写一条，页面据此展示上次结果与失败原因。
        """
        CREATE TABLE IF NOT EXISTS job_runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            job         TEXT NOT NULL,
            env         TEXT NOT NULL,
            trigger     TEXT NOT NULL DEFAULT 'schedule',
            started_at  TEXT NOT NULL,
            finished_at TEXT,
            ok          INTEGER NOT NULL DEFAULT 0,
            detail      TEXT,
            created_at  TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_job_runs_job ON job_runs(job, id)",
    ]),
    (24, "关单经验台账（只记经验，不写 facts）", [
        # 验收记录是"这次怎么交付的"，不是客户事实；单独成表，避免污染 facts 与 S1–S4 来源口径。
        """
        CREATE TABLE IF NOT EXISTS experiences (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id      TEXT NOT NULL,
            project_id   TEXT,
            role         TEXT NOT NULL DEFAULT '',
            did          TEXT NOT NULL DEFAULT '',
            result       TEXT NOT NULL DEFAULT '',
            reuse        TEXT NOT NULL DEFAULT '',
            evidence     TEXT NOT NULL DEFAULT '',
            content_hash TEXT NOT NULL DEFAULT '',
            created_at   TEXT NOT NULL
        )
        """,
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_experiences_task ON experiences(task_id)",
        "CREATE INDEX IF NOT EXISTS idx_experiences_project ON experiences(project_id, id)",
    ]),
    (25, "HR 事件类型 + 订阅注册（T-0032）", [
        # events / subscriptions 的 CHECK 约束引用 EVENT_TYPE；新增事件类型必须重建表。
        f"""
        CREATE TABLE events_v25 (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            type        TEXT NOT NULL CHECK (type IN {_in(EVENT_TYPE)}),
            task_id     TEXT REFERENCES tasks(id),
            payload     TEXT NOT NULL DEFAULT '{{}}',
            dedup_key   TEXT NOT NULL UNIQUE,
            created_at  TEXT NOT NULL,
            delivered_at TEXT
        )
        """,
        """
        INSERT INTO events_v25(id, type, task_id, payload, dedup_key, created_at, delivered_at)
        SELECT id, type, task_id, payload, dedup_key, created_at, delivered_at FROM events
        """,
        "DROP TABLE events",
        "ALTER TABLE events_v25 RENAME TO events",
        "CREATE INDEX idx_events_pending ON events(delivered_at, created_at)",
        # subscriptions 同样重建以带上新事件类型
        f"""
        CREATE TABLE subscriptions_v25 (
            event_type TEXT NOT NULL CHECK (event_type IN {_in(EVENT_TYPE)}),
            target     TEXT NOT NULL,
            action     TEXT NOT NULL,
            enabled    INTEGER NOT NULL DEFAULT 0,
            budget     INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            PRIMARY KEY (event_type, target, action)
        )
        """,
        """
        INSERT INTO subscriptions_v25(event_type, target, action, enabled, budget, created_at)
        SELECT event_type, target, action, enabled, budget, created_at FROM subscriptions
        """,
        "DROP TABLE subscriptions",
        "ALTER TABLE subscriptions_v25 RENAME TO subscriptions",
    ]),
]


def claim_outbound(event_id, kind, chat_id=""):
    """出站去重：同一事件 + 同一类型只发一次。返回 True 表示本次可以发。"""
    if not event_id or not kind:
        return True
    con = connect()
    try:
        con.execute(
            "CREATE TABLE IF NOT EXISTS outbound_sent("
            " event_id TEXT NOT NULL, kind TEXT NOT NULL, chat_id TEXT,"
            " message_id TEXT, sent_at REAL NOT NULL,"
            " PRIMARY KEY(event_id, kind))")
        cur = con.execute(
            "INSERT OR IGNORE INTO outbound_sent(event_id, kind, chat_id, sent_at)"
            " VALUES (?, ?, ?, ?)", (event_id, kind, chat_id, time.time()))
        con.commit()
        return cur.rowcount == 1
    finally:
        con.close()


def mark_outbound(event_id, kind, message_id=""):
    """补记飞书 message_id，便于对账。"""
    if not event_id or not kind:
        return
    con = connect()
    try:
        con.execute(
            "UPDATE outbound_sent SET message_id = ? WHERE event_id = ? AND kind = ?",
            (message_id, event_id, kind))
        con.commit()
    finally:
        con.close()


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def content_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


NETWORK_PATH_MARKERS = ("/Volumes/", "/Network/", "iCloud Drive", "OneDrive",
                        "Dropbox", "Google Drive")
_NETWORK_WARNED = set()


def _warn_if_network(path):
    """WAL 依赖同机 -shm 文件；网络盘/云盘上可能同步滞后，先提示风险。"""
    key = str(path)
    if key in _NETWORK_WARNED:
        return
    if any(marker in key for marker in NETWORK_PATH_MARKERS):
        _NETWORK_WARNED.add(key)
        print(f"[警告] 数据库位于网络盘/云盘：{key}；WAL 的 -shm 文件可能不同步，"
              "建议把库放本地盘，或改用 DELETE 日志模式。", file=sys.stderr)


def connect(path=None):
    p = Path(path or DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    _warn_if_network(p)
    con = sqlite3.connect(p, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA synchronous = NORMAL")
    # 单写库：并发写时最多等 15s，锁冲突先等待再失败
    con.execute("PRAGMA busy_timeout = 15000")
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
    # 重建表迁移（如 tasks v6）会触发被引用表的隐式删除检查，迁移期间必须关外键。
    con.commit()
    con.execute("PRAGMA foreign_keys = OFF")
    try:
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
    finally:
        con.commit()
        con.execute("PRAGMA foreign_keys = ON")
    # 内置嵌入模型注册（已实测）
    with con:
        con.execute(
            "INSERT OR IGNORE INTO embedding_models(model, dim, normalize, provider, state, added_at)"
            " VALUES ('bge-m3', 1024, 1, 'ollama', 'active', ?)",
            (now(),),
        )
        con.execute(
            "INSERT OR IGNORE INTO embedding_models(model, dim, normalize, provider, state, added_at)"
            " VALUES ('hash-tfidf-512', 512, 1, 'local', 'active', ?)",
            (now(),),
        )
    return applied


TABLES = [
    "projects", "tasks", "events", "facts", "reminders", "feishu_tasks",
    "docs", "agent_runs", "dead_letter", "config_kv",
    "wm_session", "embedding_models", "embeddings",
    "quotes", "contracts", "invoices", "payments", "costs", "clients",
    "deliveries", "content_plan", "users",
    "entities", "relations", "subscriptions", "job_runs",
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
