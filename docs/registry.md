# 登记表｜Agent 平台（自动生成）

> 本文件由 `python3 tools/gen_registry.py` 从现有源头生成，**不要手改**；
> 校验是否过期：`python3 tools/gen_registry.py --check`。
> 本项目同时存在老蓝图（legacy db.ROLE）和新实装（Platform Registry PostgreSQL）两套 Agent 定义，SSOT 为新实装。详见 `agents/ontology.md` 架构升级说明。

## 1. Agent 角色

### 1.1 新实装（Platform Registry PostgreSQL）— SSOT

| agent_id | version | name | 角色卡 `agents/<id>/ROLE.md` |
|---|---|---|---|
| `finance` | v7 | 财务专员 | ✓ |
| `hr` | v12 | HR | ✓ |
| `local-assistant` | v7 | 本地助手 | ✓ |
| `ops` | v3 | 运维专员（服务器运维+记账） | ✓ |
| `test-ops` | v5 | 系统运维负责人 | ✓ |

### 1.2 老蓝图（legacy `db.ROLE`）— 理想形态，未全部落地

| 角色 | 角色卡 `agents/<role>/ROLE.md` |
|---|---|
| `chief-of-staff` | ✓ |
| `auditor` | ✓ |
| `pm` | ✓ |
| `planner` | ✓ |
| `secretary` | ✓ |
| `coach` | ✓ |
| `scout` | ✓ |
| `content` | ✓ |
| `finance` | ✓ |
| `producer` | ✓ |

## 2. 本体（`agents/ontology.md`）

- 版本：v1.2｜更新：2026-11-20
- §2.1 角色（5）：`finance`、`hr`、`local-assistant`、`ops`、`test-ops`
- 事件类型（13）：`agent_circuit_break`、`agent_low_output`、`agent_role_overlap`、`agent_run`、`agent_subscribe_flood`、`invoice_overdue`、`project_created`、`quarterly_review`、`task_blocked`、`task_closed`、`task_created`、`task_due_soon`、`task_overdue`

## 3. 指令注册表（`feishu_commands.COMMAND_SPECS`，legacy）

| 指令 | 说明 |
|---|---|
| `/brief` | 一页态势（任务 / 临期 / 闸门） |
| `/roundtable <议题> [--roles a,b,c] [--rounds N] [--project X]` | 圆桌会议：多角色讨论并收敛结论 |
| `/run <角色> [任务ID] [附加指令]` | 唤醒某个 Agent |
| `/plan [--project X] [--days N] <目标>` | 把目标拆成子任务并入库 |
| `/task add <标题> --owner <角色> [--days N] [--project X] [--check <验收命令>]` | 快速建一条任务 |
| `/dispatch [--limit N] [--project X]` | 推进一轮派活（筛选 ready 任务） |
| `/sync [--force]` | 推进一轮事件投递 |
| `/help [--verbose]` | 看这份说明 |

## 4. 模型限额（`agency/limits.json`，legacy）

实测日期：2026-11-12
口径：递进式实测得出（每次请求记录真实 prompt_tokens）；数值为保守值，宁慢勿 429

| 角色 | 可用模型 |
|---|---|
| `pm` | glm-5.2 |
| `auditor` | glm-5.2 |
| `content` | glm-5.2 |
| `chief-of-staff` | glm-5.2 |
| `qa` | glm-5.2 |

| 模型 | 可调用 | 最大 prompt | TPM | RPM | 并发 | 恢复(s) |
|---|---|---:|---:|---:|---:|---:|
| `sensenova-6.8-flash-lite` | ✓ | 32000 | 18000 | 60 | 4 | 60 |
| `glm-5.2` | ✓ | 24000 | 10000 | 10 | 2 | 60 |
| `deepseek-v4-flash` | ✓ | 6000 | 4000 | 4 | 1 | 60 |
| `sensenova-6.8-flash` | ✓ | 8000 | 6000 | 6 | 2 | 60 |
| `sensenova-u1.5-lite` | ✗ | 0 | 0 | 0 | 0 | 0 |

## 5. 数据模型（`docs/erd.md`，由 `tools/erd.py` 生成）

| 表 | 列数 | 外键数 |
|---|---:|---:|
| `agent_runs` | 11 | 1 |
| `clients` | 7 | 0 |
| `config_kv` | 2 | 0 |
| `content_plan` | 13 | 0 |
| `contracts` | 11 | 3 |
| `conversations` | 5 | 0 |
| `costs` | 8 | 1 |
| `dead_letter` | 11 | 1 |
| `deliveries` | 7 | 0 |
| `dependencies` | 11 | 0 |
| `docs` | 7 | 0 |
| `embedding_models` | 6 | 0 |
| `embeddings` | 13 | 1 |
| `entities` | 7 | 1 |
| `events` | 7 | 1 |
| `experiences` | 10 | 0 |
| `facts` | 9 | 1 |
| `feishu_seen` | 2 | 0 |
| `feishu_tasks` | 6 | 1 |
| `invoices` | 8 | 1 |
| `job_runs` | 9 | 0 |
| `outbound_sent` | 5 | 0 |
| `payments` | 7 | 1 |
| `projects` | 9 | 0 |
| `quotes` | 11 | 2 |
| `relations` | 5 | 0 |
| `reminders` | 6 | 1 |
| `schema_version` | 3 | 0 |
| `subscriptions` | 6 | 0 |
| `tasks` | 21 | 1 |
| `users` | 6 | 0 |
| `wm_session` | 13 | 1 |

## 6. 交付导出清单（`LEGACY_EXPORT_FILES` / `EXPORT_DIRS`，legacy）

文件（0）：

目录（0）：

未入包（0）：无

## 7. 工具脚本（`tools/*.py`）

| 脚本 | 用途（docstring 首行） |
|---|---|
| `tools/check_consistency.py` | 结构校验 + 三方一致性检查（分项 13.3 / 13.4 / 16.3） |
| `tools/coverage_report.py` | 用标准库 trace 跑全量测试并产出 coverage.json。 |
| `tools/debt_check.py` | 技术债验收命令库（debt sync 生成的 done_when 都调用本文件） |
| `tools/feishu_env.py` | 多维表格凭据按环境取：dev 没有独立表就拒绝，绝不写正式表。 |
| `tools/gen_registry.py` | 登记表生成器：从现有源头汇总，不手写第二份真相。 |

## 8. 顶层模块（`*.py`）

| 模块 | 用途 | 导出包 | 被这些模块引用 |
|---|---|---|---|

## 9. 一致性对账（生成时计算）

### 9.1 Platform Registry ↔ ontology §2.1

- ✓ ontology §2.1 角色（5） ↔ Platform Registry agent（5）

### 9.2 Platform Registry ↔ ROLE.md 目录

- ✓ Platform Registry agent（5） ↔ ROLE.md 目录（14）；ROLE.md 有但 Platform 未注册：auditor、chief-of-staff、coach、content、planner、pm、producer、scout、secretary

### 9.3 老蓝图（legacy db.ROLE）↔ ontology

- ✗ 老蓝图（10） ↔ ontology 本体（5）；老蓝图有但 ontology 未记录：auditor、chief-of-staff、coach、content、planner、pm、producer、scout、secretary（蓝图与实装不一致不自动标红，蓝图描述理想形态）
- 本体事件类型（13）类 ↔ 老蓝图 EVENT_TYPE（28）类

- ✓ 被引用的顶层模块全部入导出包
