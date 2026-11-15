# 登记表｜Agent 平台（自动生成）

> 本文件由 `python3 tools/gen_registry.py` 从现有源头生成，**不要手改**；
> 校验是否过期：`python3 tools/gen_registry.py --check`（已接入 `tools/check_consistency.py`）。

## 1. 角色注册表（`db.ROLE`）

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
| `hr` | ✓ |
| `finance` | ✓ |
| `producer` | ✓ |

## 2. 本体（`agents/ontology.md`）

- 版本：v1.1｜更新：2026-11-11
- 2.1 角色（11）：Chief of Staff（幕僚长）、Auditor（审计）、PM（产品经理）、Planner（规划师）、Secretary（秘书）、Coach（教练）、Scout（商机侦察）、Content（内容）、HR、Finance（财务）、Producer（制作人）
- 3.6 事件类型（23）：`agent_run`、`audit_flag`、`check`、`contract_signed`、`dependency_overdue`、`doc_ready_review`、`gate_blocked`、`gate_confirmed`、`gate_revoked`、`invoice_overdue`、`milestone_deviation`、`milestone_hit`、`project_confirmed`、`project_created`、`sync_conflict`、`task_blocked`、`task_canceled`、`task_closed`、`task_created`、`task_due_soon`、`task_overdue`、`task_reopened`、`tasks_created`

## 3. 指令注册表（`feishu_commands.COMMAND_SPECS`）

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

## 4. 模型限额（`agency/limits.json`）

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

## 6. 交付导出清单（`agency.EXPORT_FILES` / `EXPORT_DIRS`）

文件（25）：`agency.py`、`db.py`、`memory.py`、`vectors.py`、`browser.py`、`gui.py`、`policy.py`、`probe.py`、`llm_local.py`、`ratelimit.py`、`feishu_bot.py`、`feishu_commands.py`、`intent.py`、`listener.py`、`console.py`、`boss.py`、`conversation.py`、`console.html`、`run-listener.sh`、`启动服务.command`、`README.md`、`CONSTITUTION.md`、`requirements-listener.txt`、`agency/config.json`、`agency/limits.json`

目录（8）：`agents`、`company`、`templates`、`tools`、`tests`、`agency/apps`、`agency/policies`、`agency/plans`

未入包（2）：`alerts.py`、`publisher.py`

## 7. 工具脚本（`tools/*.py`）

| 脚本 | 用途（docstring 首行） |
|---|---|
| `tools/bench_vectors.py` | 向量检索基准：合成 N 条向量，测冷/热查询 P95。 |
| `tools/check_consistency.py` | 结构校验 + 三方一致性检查（分项 13.3 / 13.4 / 16.3） |
| `tools/coverage_report.py` | 用标准库 trace 跑全量测试并产出 coverage.json。 |
| `tools/debt_check.py` | 技术债验收命令库（debt sync 生成的 done_when 都调用本文件） |
| `tools/erd.py` | tools/erd.py — 从 SQLite 反推 ER 图（Mermaid） |
| `tools/feishu_env.py` | 多维表格凭据按环境取：dev 没有独立表就拒绝，绝不写正式表。 |
| `tools/gen_registry.py` | 登记表生成器：从现有源头汇总，不手写第二份真相。 |
| `tools/migrate_fields.py` | 存量任务字段规范化迁移（分项 13.1 / 13.2 / 13.5） |
| `tools/schedule_wbs.py` | WBS 完成时刻排程：按角色容量算出 200 条分项的完成时分，回写 DB / 04 / 09 / 飞书。 |
| `tools/sync_plan.py` | 四处同步：DB → 04 / 09 / 飞书。默认只检查，--apply-* 才写。 |

## 8. 顶层模块（`*.py`）

| 模块 | 用途 | 导出包 | 被这些模块引用 |
|---|---|---|---|
| `agency.py` | agency.py — 龍奕FDE工作室 · Agent 公司运行底座 | ✓ | `console` |
| `alerts.py` | alerts.py — 本地告警（不依赖飞书） | ✗ | — |
| `boss.py` | boss.py — 面向创始人的两种声音（系统通知 / 角色汇报） | ✓ | `agency`、`listener` |
| `browser.py` | browser.py — 浏览器后端（P3 · T-0025）：OpenClaw browser 适配器。 | ✓ | `agency` |
| `console.py` | console.py — 本地操作控制台（只绑 127.0.0.1，纯标准库） | ✓ | — |
| `conversation.py` | conversation.py — 按 open_id 的多轮上下文与待确认意图（纯标准库） | ✓ | `listener` |
| `db.py` | db.py — 底座表结构与迁移 | ✓ | `agency`、`console`、`conversation`、`feishu_commands`、`listener`、`memory`、`vectors` |
| `feishu_bot.py` | feishu_bot.py — 飞书机器人（应用身份）最小客户端 | ✓ | `listener` |
| `feishu_commands.py` | feishu_commands.py — 飞书指令路由（T-0021） | ✓ | `listener` |
| `gui.py` | gui.py — 电脑操作层（P3 · T-0022）：应用注册表、会话、预检、只读感知。 | ✓ | `agency`、`browser`、`policy`、`probe` |
| `intent.py` | intent.py — 意图识别唯一入口 | ✓ | `console`、`listener` |
| `listener.py` | listener.py — 飞书长连接监听（P1） | ✓ | — |
| `llm_local.py` | llm_local.py — 本地模型（P3 · T-0027）：Ollama 直连，不引 SDK。 | ✓ | `agency`、`policy` |
| `memory.py` | memory.py — 结构化工作记忆（wm_session 六槽位） | ✓ | `agency` |
| `policy.py` | policy.py — 策略层与模型选择器（P3 · T-0027）。 | ✓ | `agency` |
| `probe.py` | probe.py — 探针能力包（P3 · T-0026）：未知应用的一次性可行性勘查。 | ✓ | `agency` |
| `publisher.py` | 平台发布适配接口（空实现）。 | ✗ | — |
| `ratelimit.py` | ratelimit.py — 按 endpoint 的本地限流（token 预算 + 请求速率 + 并发闸门） | ✓ | `agency` |
| `vectors.py` | vectors.py — 嵌入与向量检索 | ✓ | `agency` |

## 9. 一致性摘要（生成时计算）

- ✓ 角色：`ROLE` 11 个 ↔ `agents/*/ROLE.md` 11 个
- ✓ 本体 2.1 角色 11 个 ↔ `ROLE` 11 个
- ✓ 本体 3.6 事件 23 类 ↔ `EVENT_TYPE` 23 类
- ✓ 被引用的顶层模块全部入导出包；未入包但无引用：alerts.py、publisher.py
