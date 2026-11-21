# AI 原生运行底座 · 领域本体（Ontology）

- 版本：v1.2
- 更新：2026-11-20
- 定位：所有 Agent 角色、数据模型、事件语义的集中式参照；代码层校验规则与提示词组装都以此为源
- 引用：[公司宪法](../company/公司宪法.md)、[06 技术方案](../clients/002-FDE一人公司/06-技术方案.md)

---

## 架构升级说明（v1.1 → v1.2）

本项目经历了**两套 Agent 管理架构的迁移**，本文档与代码层以新架构为准：

| | 老架构（OPC 一人公司蓝图） | 新架构（当前实装） |
|---|---|---|
| Agent 定义 | 硬编码在 `agents/<role>/ROLE.md` + `db.ROLE` 枚举 | 动态注册在 PostgreSQL `platform_registry_active` 表 |
| 角色数量 | 11 个（幕僚长、审计、PM、规划师、秘书、教练、商机侦察、内容、财务、制作人） | 5 个（以 API `/api/platform/v1/registry/agent` 返回为准） |
| 事件驱动 | 声明式「事件类型 → 角色」订阅模型 | 事件走 EventBus → Worker → 手动/人工 dispatch 给某个 agent |
| 协作方式 | 黑板模型（共享 SQLite 状态表） | Agent 之间通过 `task_dispatch` 工具转派任务 |

**单一事实源（SSOT）**：
- Agent 角色清单与定义 → `platform_registry_active` 表（API `/api/platform/v1/registry/agent`）
- 事件订阅配置 → 暂无自动订阅，当前事件驱动靠人工或 Worker 手动 dispatch

**公司治理蓝图**（组织架构、宪法、理想角色分工）不在本文档维护，参见 `company/组织架构.md` 和 `company/公司宪法.md`。蓝图描述的是"理想组织形态"，Registry 描述的是"当前实装"，两者不一致不代表蓝图错了。

---

## 1. 术语表（Glossary）

每个术语一句话定义 + 一个反例，供 Agent 在对话、输出中对齐用词。

| 术语 | 定义 | 反例 |
|---|---|---|
| FDE | Founder-Driven Execution，创始人驱动执行，一人公司模式 | 反例：多人协作的职能型公司 |
| OPC | One-Person Company，一人公司 | 反例：有多个全职员工的初创团队 |
| Agent（智能体） | Registry 中注册的可独立执行任务的角色实例，有 agent_id、name、description、instructions、tool_ids、skill_ids | 反例：硬编码在 ROLE.md 里但 Registry 不存在的角色定义 |
| 项目（Project） | 客户签约或内部立项的、有明确目标和关闭条件的交付单元 | 反例："帮客户看看系统"——没有目标和关闭条件 |
| 任务（Task） | 项目下可独立执行、有 owner 有截止、可验收的最小工作单元 | 反例："做一下客户的 AI 系统"——不可独立验收、无 owner |
| 事件（Event） | 状态变化产生的信号，由 EventBus 发布，Worker 可据此派活 | 反例："每天早上 9 点我自己看看"——不是事件驱动 |
| 事实（Fact） | 有明确来源的陈述，可追溯到 S1-S4 | 反例："客户大概有 20 人"——无来源 |
| 死信（Dead Letter） | Agent 调用连续失败的事件，入队不丢，等人工处理 | 反例：Agent 报错后静默丢弃事件 |
| 任务转派（dispatch） | Agent 通过 `task_dispatch` 工具把活派给 Registry 里的另一个 agent，等对方跑完把结果带回 | 反例：Agent A 发消息问 Agent B，B 回答后 A 再继续——对话式闲聊 |
| Registry | PostgreSQL `platform_registry_*` 表集合，存放 Agent、Tool、Skill、Model 模板等运行时定义 | 反例：硬编码在 Python 文件里的 agent 列表 |
| schema_version | 数据库表结构版本号，迁移脚本以此判断是否需要升级 | 反例：直接手动 `ALTER TABLE` 改结构，不记录版本 |

---

## 2. 实体定义（Entities）

### 2.1 Agent 角色（当前 Registry 实装 5 个）

**单一事实源**：`/api/platform/v1/registry/agent` 返回的 `platform_registry_active` 表。

| agent_id | version | name | 定位 | 主要工具 |
|---|---|---|---|---|
| `finance` | v7 | 财务专员 | 收入/支出/账目/流水/记账/报表/预算管理 | task_dispatch、ledger_create、ledger_add_entry、ledger_query、ledger_report |
| `hr` | v12 | HR | Agent 团队招聘负责人：招新/招聘/入职/角色分配/绩效；可直接创建/更新 Agent | registry_list_agents、registry_create_agent、registry_update_agent、task_dispatch |
| `local-assistant` | v7 | 本地助手 | 老板的万能助理：能查账本、做报表、看团队、帮其他 agent 转派 | task_dispatch、ledger_create、ledger_query、ledger_report、registry_list_agents、registry_list_tools |
| `ops` | v3 | 运维专员（服务器运维+记账） | 运维服务器 + 记账 | registry_list_agents、registry_list_tools、task_dispatch、ledger_create、ledger_add_entry、ledger_query |
| `test-ops` | v5 | 系统运维负责人 | 系统监控、故障排查、报警处理、服务状态检查、环境维护 | task_dispatch |

**漂移记录（2026-11-20 发现）**：

1. ~~`ops` v2 和 `test-ops` v4 的 name 都叫"运维专员"，意图匹配时会产生歧义~~。**已治理（2026-11-20）**：ops → v3 改名"运维专员（服务器运维+记账）"，test-ops → v5 改名"系统运维负责人"。
2. 老蓝图的 11 个角色（幕僚长、审计、PM、规划师、秘书、教练、商机侦察、内容、制作人）**均未在 Registry 注册**——这些是"理想组织形态"，当前未落地。蓝图未落地 ≠ 蓝图错了，两者分工在 §5.7 和 docs/registry.md §9.3 已如实标注。

### 2.2 核心实体

| 实体 | 类型 | 说明 |
|---|---|---|
| Project | 交付单元 | 客户签约或内部立项，有目标、有关闭条件 |
| Task | 最小工作单元 | 项目下可独立执行、有 owner、有截止、可验收 |
| Event | 状态变化信号 | 由 EventBus 发布，Worker 可据此派活；有 dedup_key 防重 |
| Fact | 有来源的陈述 | 可追溯到 S1-S4 |
| AgentRun | Agent 运行记录 | agent_id、任务、模型、token、产出路径 |
| DeadLetter | 失败事件队列 | 连续失败的事件，不静默丢弃 |
| Agent | Registry 项 | agent_id、version、name、description、instructions、tool_ids、skill_ids |

### 2.3 实体 ID 规范

| 类型 | 前缀 | 示例 |
|---|---|---|
| 项目 | `proj:` | `proj:OPC-002` |
| 任务 | `task:` | `task:T-0016` |
| Agent | 无统一前缀 | 直接用 Registry 的 `agent_id`（如 `finance`、`hr`、`ops`、`test-ops`、`local-assistant`） |
| 文档 | `doc:` | `doc:09-分项计划` |
| 事实 | `fact:` | `fact:F-0001` |

---

## 3. 枚举与常量

### 3.1 任务状态（Task Status）

| 值 | 含义 | 可转换到 |
|---|---|---|
| `todo` | 待认领 | `doing`、`blocked` |
| `doing` | 进行中 | `review`、`blocked` |
| `blocked` | 阻塞（有原因） | `doing` |
| `review` | 待验收 | `done`、`doing`（打回） |
| `done` | 已验收 | `closed` |
| `closed` | 已关闭 | — |

### 3.2 事实来源（Source Level）

| 值 | 含义 | 能否作为陈述句 |
|---|---|---|
| `S1` | 客户原话、签字、书面确认 | ✅ 可以 |
| `S2` | 客户提供的文档、系统数据、报表 | ✅ 可以 |
| `S3` | 公开可查资料（官网、公告、报道） | ✅ 可以，须注明出处 |
| `S4` | 推断、经验、假设、听说 | ❌ 必须标注"假设，待核实" |

### 3.3 授权等级（Authorization Level）

| 值 | 允许 | 禁止 |
|---|---|---|
| `A0` 只读 | 读取全部资料 | 任何写入 |
| `A1` 起草 | 写草稿、建日程、写提醒 | 对外发送、承诺 |
| `A2` 对外 | 发送需人确认的对外消息 | 价格/工期/效果承诺 |
| `A3` 承诺 | — | **所有 Agent 都没有 A3**，仅创始人执行 |

### 3.4 事件类型（Event Type）

**说明**：以下事件类型保留自老蓝图，但**唤醒角色列标注的是当前实装状态**——绝大多数事件没有自动订阅机制，需人工或 Worker 手动 dispatch 给合适的 agent。

| 事件类型 | 触发条件 | 当前唤醒方式 |
|---|---|---|
| `project_created` | 项目立项 | 人工/控制台处理 |
| `task_created` | 新任务入库 | 人工/控制台处理 |
| `task_due_soon` | 任务距开始 ≤1 天 / 距截止 ≤2 天 | 定时巡检告警 |
| `task_overdue` | 任务已过截止日 | 定时巡检告警 |
| `task_blocked` | 任务标记阻塞 | 人工/控制台处理 |
| `task_closed` | 任务关闭 | 无自动 agent 订阅 |
| `agent_run` | Agent 调用完成 | 调度层（推进下一个 ready） |
| `invoice_overdue` | 发票到期未回款 | 人工 dispatch → finance |
| `agent_circuit_break` | Agent 连续失败 3 次触发熔断 | **当前无自动订阅**（老蓝图写"HR"，但 HR 未接线自动订阅；需人工 dispatch → hr） |
| `agent_low_output` | Agent 被唤醒 ≥10 次/周但有效产出 < 3 条 | **当前无自动订阅** |
| `agent_role_overlap` | 巡检发现两个 Agent 职责交叉 | **当前无自动订阅**（已解决案例：ops v2 和 test-ops v4 同名运维专员，已治理） |
| `agent_subscribe_flood` | 同一事件类型被 ≥3 个角色重复订阅 | **当前无自动订阅** |
| `quarterly_review` | 自然季度结束，触发全量盘点 | **当前无自动订阅** |

> 老蓝图的 23 类事件已精简为以上高频核心事件。如需完整清单见 v1.1 归档。

### 3.5 其他常量

| 常量 | 值 | 说明 |
|---|---|---|
| Agent 调用超时 | 180 秒 | 超时记失败，进 dead_letter |
| 熔断阈值 | 连续失败 3 次 | 暂停该 agent 并告警 |
| 模型 | `AGENCY_MODEL`（环境变量） | 所有 agent 通用，可按 Registry 单独配置 `model_template_id` |
| registry_list_agents 缓存 | 120 秒 | listener 侧意图匹配时缓存 agent 清单，避免每次请求 API |

---

## 4. 实体关系（Relations）

```
Project ──has──▶ Task ──generates──▶ Event
   │                  │
   │ belongs_to       │ dispatched_to
   ▼                  ▼
 Owner(agent_id)   Event ──dispatch──▶ Agent(agent_id)
```

| 关系 | 含义 | 约束 |
|---|---|---|
| `Project has Task` | 项目包含任务 | Task 必须属于且仅属于一个 Project |
| `Task has Owner` | 任务由某 agent 负责 | Owner 必须是 Registry 中存在的 `agent_id`，或"创始人"（human） |
| `Task generates Event` | 任务状态变化产生事件 | dedup_key 唯一，同一任务同一日期不重复 |
| `Agent dispatches Agent` | Agent 通过 `task_dispatch` 转派任务 | 目标 agent 必须在 Registry 中注册 |
| `Fact has SourceLevel` | 事实有来源等级 | 非法值拒绝写入 |

---

## 5. 硬约束（Hard Constraints）

以下规则由代码层写入前校验，非法值**拒绝写入，不静默修正**。

### 5.1 任务生命周期约束

| 约束 | 规则 |
|---|---|
| 状态枚举 | `tasks.status` 仅允许 `todo/doing/blocked/review/done/closed` |
| 进入 closed | 必须有 evidence 字段非空，否则拒绝 |
| blocked 退出 | 必须先清空 blocked_reason，再转回 doing |

### 5.2 Agent 身份约束

| 约束 | 规则 |
|---|---|
| Owner 合法性 | 任务 Owner 必须在 Registry 中存在，或为"创始人" |
| 自派禁止 | Agent 不得用 `task_dispatch` 把活派给自己 |
| 编造禁止 | Agent 不得编造工具执行结果、账本数据、文件路径 |

### 5.3 事实约束

| 约束 | 规则 |
|---|---|
| S4 标注 | S4 事实必须显式标注"假设，待核实"，否则拒绝入库 |
| 来源可追溯 | 写入 facts 表必须有 source_ref（来源引用），无引用拒绝 |
| 冲突处理 | 新事实覆盖旧事实，旧事实标 `superseded`，不删除 |

### 5.4 时间与时窗约束

| 约束 | 规则 |
|---|---|
| 时区 | 存储统一 ISO8601 UTC，展示层转本地时区 |
| 提醒幂等 | `dedup_key = type + task_id + date`，重复触发结果不变 |

### 5.5 ID 规则

| 类型 | 格式 | 示例 |
|---|---|---|
| 项目 | `OPC-` + 定长序号 | `OPC-001`、`OPC-002` |
| 任务 | `T-` + 定长序号 | `T-0001`、`T-0042` |
| 事实 | `F-` + 定长序号 | `F-0001` |
| Agent | Registry `agent_id` | `finance`、`hr`、`ops`、`test-ops`、`local-assistant` |
| 不允许 | ID 不复用，删除后永久废弃 | — |

### 5.6 内容指纹

所有文本入库必须带 `content_hash`（SHA-256 前 16 位），用于：
- 去重查重
- 同步比对（推送飞书前比对，未变更不重复写）
- 嵌入版本管理（`content_hash` 变化触发重嵌）

### 5.7 老蓝图约束（当前 Registry 架构尚未接线）

以下约束来自老蓝图（v1.1），描述的是"理想形态"。当前 Registry 架构没有对应的自动订阅机制，这些约束需要未来在事件驱动层落地后才生效：

| 约束 | 蓝图描述 |
|---|---|
| 审计独立 | 审计角色直属创始人，不受业务角色管理；有权冻结文档、暂停发布 |
| 闸门前置 | PM 方案确认单 → 规划师排期；无确认单禁止排期 |
| 审计放行 | 对外文档必须审计通过，否则禁止推送 |
| 确认单前置 | 客户书面确认是所有项目的起点 |

---

## 6. 事件语义详解（Event Semantics）

### 6.1 Agent 协作模型

**Registry + task_dispatch 模型。**

所有 Agent 从 Registry 动态加载定义（instructions、tool_ids、skill_ids）。Agent 之间不直接对话，而是通过 `task_dispatch` 工具转派任务：

```
Agent A 接活
  → registry_list_agents 查 Registry 看谁能做
  → task_dispatch(agent_id="finance", prompt="...") 派给 B
  → 等待 B 执行完成（最长 wait_seconds）
  → B 把结果带回给 A
  → A 整理回复
```

飞书侧意图匹配（`listener.py`）会根据用户消息自动选择最合适的 agent（显式指定 > 关键词匹配 > bigram 描述相似度 > 默认）。

### 6.2 典型事件链

```
用户飞书发消息："帮我建个账本"
  → listener.py 意图匹配（关键词"账本"命中 finance）
  → finance 被唤起
  → finance 调用 ledger_create 工具建账本
  → 返回结果给用户
```

```
ops v3 和 test-ops v5 职责有重叠（虽然名字已区分）
  → hr 巡检发现 agent_role_overlap 事件
  → hr 通过 registry_update_agent 合并或明确区分两个 agent 的职责
```

### 6.3 EventBus 生命周期

```
created → dedup 检查 → Worker 消费 → dispatched → delivered
                                        ↓
                                  dead_letter（连续失败 3 次）
```

| 阶段 | 操作 | 幂等 |
|---|---|---|
| `created` | 写入 events 表，`dedup_key` 唯一约束 | ✅ |
| `dispatched` | Worker 派活给某个 agent，单次调用 ≤180 秒 | ❌（但超时不挂死） |
| `delivered` | 成功后标记 `delivered_at` | ✅ |
| `dead_letter` | 连续 3 次失败，入队等人工处理 | ✅ |

---

## 7. 工作记忆结构

Agent 每轮调用的上下文按以下固定顺序组装（由 Registry 的 `instructions` + 注入片段合成）：

| 顺序 | 内容 | 预算 | 可否裁剪 |
|---|---|---|---|
| 1 | 系统指令（Registry `instructions` 字段） | 必带 | ❌ 永不裁剪 |
| 2 | 对话历史（最近 N 轮） | ≤16k tokens | ✅ 超预算时先压 |
| 3 | 任务上下文（如果有 task_id） | 必带 | ❌ |
| 4 | 相关事实与案例（向量检索注入） | ≤8 条 | ✅ 阈值 0.3，低相关直接不注入 |

---

## 8. 修订规则

1. 本文件修订须创始人书面批准。
2. 版本号递增，更新日期同步。
3. Agent 角色表以 `platform_registry_active` 表为 SSOT，任何新增/删除/改名 agent 后须同步更新 §2.1。
4. 代码层引用 agent_id 时，禁止硬编码与 Registry 不一致的字面值——可用 `registry_list_agents` 查询或从 SSOT 动态加载。
5. 老蓝图约束（§5.7）在事件驱动层落地后须升级为硬约束。
