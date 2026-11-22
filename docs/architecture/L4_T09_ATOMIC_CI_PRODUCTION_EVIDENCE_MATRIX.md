# L4-T09：差距审查成果、原子任务 CI 与生产验收证据矩阵

- 总控 Issue：[#848](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/848)
- 原子任务来源：[Issue #267](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/267)
- 代码基线：`main @ a1f621e3e8c3740f21c5e3cf1ed5be347b419e0d`（2026-11-22）
- 关联审查成果：A4 #863、A5 #871、B4 #872、B5 #874、C4 #866、C5 #868、D3 #859、D4 #873、D5 #877
- 状态：**证据汇总基线；不是 186 项通过报告，也不是生产放行单。**

## 1. 目的与判定原则

本矩阵把 Issue #848 的差距审查结论、Issue #267 当前原子任务明细、历史实现/PR/CI 记录和生产验收登记放到同一追踪视图。范围为 #8–#31 与 #72 中的 186 个唯一 C/A 原子编号。

严格区分四类证据：

1. **实现证据**：main 中代码、迁移、API、前端入口和关联 PR/commit。
2. **自动化证据**：具体 PR HEAD 或 main SHA 的 workflow/check-run 终态，以及实际执行的测试/产物。
3. **生产证据**：目标环境 preflight、真实部署、真实 smoke、真实数据迁移、备份恢复、回滚/故障演练和责任人签核。
4. **结论**：只有适用证据齐全才可标记完成。PR merged、复选框 checked、测试文件存在、workflow 创建或 queued 都不能单独判定通过。

Issue #267 中的 checkbox 是历史登记状态，不是本矩阵的验收结论。下表保留条目原始描述和状态摘要作为索引；历史 CI 状态需按当前 PR HEAD 刷新，不能将旧 SHA 的结果继承到当前 main。

## 2. 当前基线快照

| 检查项 | 当前观察 | 结论 |
|---|---|---|
| main SHA | `a1f621e3e8c3740f21c5e3cf1ed5be347b419e0d` | 本矩阵唯一代码基线 |
| main check-runs | 6 项：5 queued、1 in_progress | **CI pending**；不能标记 main 全量 CI pass |
| 原子编号 | 186 个唯一 ID（#8–#31、#72） | 已按 ID 去重；不含额外战略任务编号 |
| Issue #267 checkbox | 185 checked / 1 unchecked（A-12-02）为历史台账快照 | 不等于 185 项通过最新 CI 或生产验收 |
| 生产验收登记 | `docs/release/PRODUCTION_ACCEPTANCE_REGISTER.md` 总结 NO-GO | 生产放行不通过；生产证据仍有阻塞 |
| 本轮现场验证 | 未申请真实服务器、生产数据、外部凭据或真机 | 按调度跳过；未伪造生产结果 |

### 2.1 当前系统性结论

- 审查文档已整合目标架构、191 项能力映射、模块覆盖台账、测试执行记录和 54 项研发 Backlog；它们证明审查交付物存在，不证明所有原子能力已实现。
- #267 记录了多个已合并 PR 的 CI failure/cancelled/pending，以及部分无 workflow 记录的情况。已合并代码不重复开发；对应 CI/集成/生产验收仍需闭环。
- #12 的 A-12-02 是当前明确未勾选的生产 OCI 隔离项；CI 自动化探针不替代真实生产 Podman 主机的文件、网络、进程和凭据隔离验证。
- #19 的迁移工具链已有代码与测试入口，但全量源数据盘点、零差异、生产回滚/双写边界及真实切换仍需现场证据。
- #18 的备份、Outbox/Redis 恢复与对象存储机制存在代码基础，但跨组件一致性、RPO/RTO 和真实恢复演练仍未验收。
- #8–#21 的领域 CI/生产状态以各原子 Issue 当前正文和 #267 最新逐项证据为准；不得以早期审查 SHA 的成功记录覆盖新代码基线。

## 3. 生产验收总登记（权威门禁）

来源：[`docs/release/PRODUCTION_ACCEPTANCE_REGISTER.md`](../release/PRODUCTION_ACCEPTANCE_REGISTER.md)。

| 生产验收项 | 当前登记结论 | 仍需的证据 |
|---|---|---|
| Deployment preflight | BLOCKED | 责任人、目标环境依赖健康、候选 artifact digest、变更窗口、回滚准备 |
| Core E2E | AUTOMATION EVIDENCE ONLY | 最新 main CI 与适用的目标环境业务旅程证据 |
| Fault injection / recovery | AUTOMATION EVIDENCE ONLY | 操作人、故障时间线、恢复结果、数据丢失评估和不可变日志 |
| Performance / capacity | AUTOMATION EVIDENCE ONLY | 批准负载/阈值、环境与模型标识、延迟/吞吐/并发/成本/质量报告 |
| Security | AUTOMATION EVIDENCE ONLY | 安全负责人签核、最新安全 CI、生产威胁/授权复核和未决项处置 |
| Backup / restore | AUTOMATION EVIDENCE ONLY | 备份身份、隔离恢复报告、完整性检查、RPO/RTO 和操作人签核 |
| Production smoke | BLOCKED | 获批变更记录、真实 smoke 结果、时间戳、操作人及日志 |
| Migration | BLOCKED | 精确迁移集、目标版本 preflight、兼容窗口、审批与执行证据 |
| Rollback | BLOCKED | 候选 SHA、操作人、演练记录、恢复校验、回滚策略及结果 |

**总判定：NO-GO。** CI success、synthetic fixtures、mock、工作流定义和审查文档均不能将生产登记改成 passed。生产验收需由具名责任人针对精确候选 SHA 提交不可变证据，并由独立审批人签核。

## 4. 186 项原子任务逐项证据矩阵

列说明：
- “原子条目状态摘要”来自 #267 对应条目，仅供定位历史记录；不继承 checkbox 的完成含义。
- “关联 PR”是条目正文可识别的 PR 编号，不代表该 PR 当前仍是有效 CI 证据。
- “CI 当前判定”仅按条目记录中的历史描述分类；如未附项级终态则明确写未见。所有历史记录均需对照当前 PR HEAD / main SHA 复验。
- “生产验收”在没有项级目标环境证据时统一保持未验收；不以自动化测试替代。

| 原子 ID | 原子 Issue | 需求/验收项 | #267 条目状态摘要 | 关联 PR | CI 当前判定 | 生产验收 |
|---|---:|---|---|---|---|---|
| `A-8-01` | #8 | 会话、消息、文件与任务/运行之间可关联追溯。 | 状态：🔨 已 dispatch，开发中未 deliver；测试/验收证据：待补；CI/现场记录：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-8-02` | #8 | 文件访问执行资源级授权；撤销连接器授权后停止同步并按策略处理已同步数据。 | 状态：❓ A- 验收任务，需单独核验；测试/验收证据：待补；CI/现场记录：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-8-03` | #8 | 不把旧 conversation.py / docs 表直接作为新平台唯一实现，明确 Adapter 与迁移边界。 | 状态：❓ A- 验收任务，需单独核验；测试/验收证据：待补；CI/现场记录：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-8-01` | #8 | 统一会话/消息模型，支持用户、Agent、系统事件、工具结果等消息类型。 | 状态：✅ merged 到 main（PR #337）；实现与测试以该 PR 合并记录为准。 | #337 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-8-02` | #8 | 文件资产元数据、对象存储适配、上传下载、版本、哈希、病毒/内容安全检查接口。 | 状态：✅ merged 到 main（PR #494）；代码证据：待补；测试证据：待补；PR/Commit：待补；CI：待补。 | #494 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-8-03` | #8 | 文件解析生命周期：持久化任务、租约回收/重试/死信、Parser Registry、解析结果与失败可观测性。 | 状态：✅ merged 到 main（PR #327）；代码证据：`agent_platform/control_plane/file_processing.py`（PostgreSQL 持久化队列、Worker、租约过期接管、退避重试、死信、结果及失败诊断）；`agent_platform/storage/postgres/027_file_processing_jobs.sql`（任务/租约表）；`agent_platform/storage/postgres/045_file_processing_obser | #327 | 历史 CI 有失败/取消记录；须按最新 PR HEAD 复验 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-8-04` | #8 | 外部数据连接器契约：OAuth/凭据引用、同步游标、增量同步、重试、撤销授权与下游清理。 | 状态：✅ merged 到 main（PR #329）；代码证据：`agent_platform/control_plane/data_connectors.py`（tenant-scoped secret:// 凭据引用、Provider 契约、游标 CAS、增量同步、撤销）；复用 `agent_platform/control_plane/postgres_connectors.py`、`http_connector.py`、`connector_cleanup.py` 及迁移 `012_connector_s | #329 | 条目含历史成功记录；须核对最新 HEAD 与覆盖范围 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-8-05` | #8 | 会话与文件均支持项目/租户范围和审计追踪。 | 状态：✅ merged 到 main（PR #333）；代码证据：待补；测试证据：待补；PR/Commit：待补；CI：待补。 | #333 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-9-01` | #9 | 服务重启后可恢复可恢复任务，不重复执行已确认的外部副作用。 | 状态：❓ A- 验收任务，需单独核验；测试/验收证据：待补；CI/现场记录：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-9-02` | #9 | 用户能查询、取消和跟踪运行；人工介入后可从明确检查点继续。 | 状态：❓ A- 验收任务，需单独核验；测试/验收证据：待补；CI/现场记录：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-9-03` | #9 | 运行上下文快照/恢复保留目标、约束、决策、证据和待办，且可追溯；Token 超限策略由 #72 验收。 | 状态：❓ A- 验收任务，需单独核验；测试/验收证据：待补；CI/现场记录：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-9-01` | #9 | 统一任务与运行状态机、队列、优先级、取消、暂停、恢复、重试和超时。 | 状态：✅ merged 到 main（PR #324）；代码证据：`agent_platform/control_plane/tasks.py`（取消确认、过期取消租约回收）、`agent_platform/runtime/lifecycle.py`（Run 状态机、暂停/恢复、超时与终态语义）、`agent_platform/runtime/postgres_lifecycle.py`（`platform_runs` 优先级领取、租户可选过滤、快照继承重试）、`agent_platform/runtime/wor | #324 | 历史 CI 有失败/取消记录；须按最新 PR HEAD 复验 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-9-02` | #9 | 运行详情、输入输出、步骤事件、日志、产物、审批状态和错误信息。 | 状态：✅ merged 到 main（PR #328）；代码证据：`agent_platform/runtime/interventions.py`（租户隔离的 Run 详情聚合、步骤/日志/产物/失败事件持久化、审批关联、敏感字段脱敏）；测试证据：`tests/test_runs_interventions_api.py`、`tests/test_task_center_interventions_postgres_integration.py`（API 输入输出/步骤/日志/产物/审批/Task+Trace 关 | #328 | 历史 CI 有失败/取消记录；须按最新 PR HEAD 复验 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-9-03` | #9 | Worker 可靠性：租约、心跳、崩溃接管、重复投递幂等、Checkpoint 恢复和死信处理。 | 状态：✅ merged 到 main（PR #330）；代码证据：`agent_platform/runtime/workbench_tasks.py`（全执行阶段周期心跳、失租取消、Checkpoint `resume_claimed`）；`agent_platform/runtime/postgres_lifecycle.py`（`SKIP LOCKED` 领取、租约续期/失租 fencing、原子领取次数、有界重试、死信写入）；`agent_platform/storage/postgres/044_work | #330 | 条目含历史成功记录；须核对最新 HEAD 与覆盖范围 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-9-04` | #9 | 人工协同：上下文补充、决策/审批、结果修订、接管、继续执行及操作留痕。 | 状态：✅ merged 到 main（PR #331）；代码证据：`agent_platform/runtime/interventions.py`（暂停 Run 的上下文/决策/审批/接管请求；显式 `approved`/`rejected`/`needs_changes` 决议；未决审批不进入 Checkpoint applier，未决或被拒绝的决议阻止继续执行；审批通过后在恢复前应用到图状态；成功 Run 结果修订，事务内保留脱敏前后结果、操作人和原因；接管、决议、应用、修订及继续执行写入 Run 事件）；` | #331 | 条目含历史成功记录；须核对最新 HEAD 与覆盖范围 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-9-05` | #9 | 运行上下文组装、快照/引用窗口与恢复时上下文重建；Token 预算策略由 #72 负责。 | 状态：✅ merged 到 main（PR #530）；实现与测试以该 PR 合并记录为准。 | #530 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-10-01` | #10 | 能从上传/连接器接入到解析、索引、检索、引用回答形成闭环。 | 状态：🔨 验收测试已提交 PR [#610](https://github.com/dujiu1024-dotcom/Agent-pingtai/pull/610)（已合并到 main）；head `bd8687c485e356ce04ca58523c560a29c6f43ab0`。新增 `tests/test_issue_10_a_10_01_ingestion_rag_acceptance.py`：端到端覆盖 Markdown 上传→解析→切片→Embedding 持久化→检索→带来源定位的引用回答；连接器增 | #610 | 历史 CI 有失败/取消记录；须按最新 PR HEAD 复验 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-10-02` | #10 | 检索结果必须经过租户和文档权限过滤，不能先召回后暴露未授权片段。 | 状态：🔨 验收测试已提交 PR [#613](https://github.com/dujiu1024-dotcom/Agent-pingtai/pull/613)（已合并到 main）；head `488c9788cc4de813b9f4dfcdf297258c300103cf`。新增 `tests/test_issue_10_a_10_02_acl_before_recall.py`：覆盖 PostgreSQL lexical/pgvector 两路 SQL 均在 WHERE 阶段包含 tenant、最新文档 | #613 | 历史 CI 有失败/取消记录；须按最新 PR HEAD 复验 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-10-03` | #10 | 回答可追溯到文档版本、片段、页码/定位和检索链路。 | 状态：🔨 自动化验收已提交 PR [#616](https://github.com/dujiu1024-dotcom/Agent-pingtai/pull/616)（已合并到 main）；HEAD `bdebedb6ef289127eb18a7aaa689960ebd36c189`。实现证据：`agent_platform/knowledge/service.py` 为每次检索生成/沿用 retrieval_id，并将同一 ID 关联 RAG 与 knowledge.retrieve span；每个 hit 附 | #616 | 历史 CI 有失败/取消记录；须按最新 PR HEAD 复验 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-10-04` | #10 | 明确旧 vectors.py 的复用/迁移策略；pgvector 仅是底座，不视为知识库已完成。 | 状态：🔨 验收策略与架构回归测试已提交 PR [#628](https://github.com/dujiu1024-dotcom/Agent-pingtai/pull/628)（已合并到 main），head `7f6b066f73c9ed91ae6c54660d1200490d3b0f9f`。核验现状：旧 `vectors.py` 依赖顶层 `db.py`/本地 SQLite embeddings，包含 Ollama dense 与 hash-TF fallback；新平台使用 `KnowledgeServi | #628 | 历史 CI pending/queued；须复验终态 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-10-01` | #10 | 知识库/集合/文档/版本/分块/索引/权限等领域模型和管理 API。 | 状态：✅ merged 到 main（PR #498）；实现与测试以该 PR 合并记录为准。 | #498 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-10-02` | #10 | 文档接入与解析（PDF、Office、HTML、Markdown、文本及扫描件 OCR 扩展接口）。 | 状态：✅ merged 到 main（PR #507）；实现与测试以该 PR 合并记录为准。 | #507 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-10-03` | #10 | 清洗、切片、元数据、来源定位、内容哈希、增量更新和删除。 | 状态：❌ 未派发，无 PR 记录；代码证据：待补；测试证据：待补；PR/Commit：待补；CI：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-10-04` | #10 | Embedding 模型适配与版本管理；PostgreSQL + pgvector 向量检索，PostgreSQL 全文检索混合召回。 | 状态：✅ merged 到 main（PR #395）；实现与测试以该 PR 合并记录为准。 | #395 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-10-05` | #10 | 可插拔 Rerank、查询改写、过滤和检索策略。 | 状态：✅ merged 到 main（PR #514）；实现与测试以该 PR 合并记录为准。 | #514 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-10-06` | #10 | 文档/片段级 ACL 过滤、引用溯源、权限变更与删除传播。 | 状态：❌ 未派发，无 PR 记录；代码证据：待补；测试证据：待补；PR/Commit：待补；CI：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-10-07` | #10 | RAG 评测集和召回/准确性/引用正确性评测。 | 状态：✅ merged 到 main（PR #529）；实现与测试以该 PR 合并记录为准。 | #529 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-11-01` | #11 | 不将聊天全文无差别写入长期记忆。 | 状态：🔨 已有候选提炼/确认工作流（历史 PR #604 已 merged）；本轮补齐服务端防线，防止 extractor 将用户消息、助手消息或两者拼接原文作为候选写入。代码：`agent_platform/memory/extraction.py` 在任何写入前对候选与来源消息（含拼接轮次）做空白归一化比对，命中原文即拒绝；测试：`tests/test_issue_11_a_11_01_memory_distillation.py` 新增用户消息原文与整轮拼接原文拒绝用例，并断言无部分候选落库；既有测试继续覆 | #604, #630 | 历史 CI pending/queued；须复验终态 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-11-02` | #11 | 记忆检索按用户/租户/项目权限隔离。 | 状态：🧪 自动化 API 验收测试已提交 PR [#633](https://github.com/dujiu1024-dotcom/Agent-pingtai/pull/633)，HEAD `733b204bbf94756bb5276c032fea004b6f6409b1`，已合并到 main。核查实现：`MemoryService._visible` 在 tenant 匹配后按 scope 执行用户 ID、project_ids、tenant-wide 可见性过滤；`MemoryService.retriev | #633 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-11-03` | #11 | 记忆可追溯来源，并支持更新、过期和删除。 | 状态：❓ A- 验收任务，需单独核验；测试/验收证据：待补；CI/现场记录：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-11-01` | #11 | 区分运行短期记忆、会话记忆、用户长期记忆、Agent/项目记忆和组织共享记忆。 | 状态：✅ merged 到 main（PR #362）；代码证据：待补；测试证据：待补；PR/Commit：待补；CI：待补。 | #362 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-11-02` | #11 | 记忆提取、候选确认、冲突处理、更新/遗忘、过期、归档和检索策略。 | 状态：✅ merged 到 main（PR #366）；代码证据：待补；测试证据：待补；PR/Commit：待补；CI：待补。 | #366 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-11-03` | #11 | 记忆来源、置信度、时间、适用范围、权限和版本记录。 | 状态：✅ merged 到 main（PR #373）；代码证据：待补；测试证据：待补；PR/Commit：待补；CI：待补。 | #373 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-11-04` | #11 | 记忆与知识库、Blackboard、会话历史之间建立明确边界与引用关系。 | 状态：✅ merged 到 main（PR #510）；实现与测试以该 PR 合并记录为准。 | #510 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-11-05` | #11 | 用户可查看、纠正、导出和删除其有权管理的记忆；删除传播到索引/缓存。 | 状态：✅ merged 到 main（PR #501）；实现与测试以该 PR 合并记录为准。 | #501 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-12-01` | #12 | Harness 候选不以单次 smoke test 作为生产选型结论。 | 状态：🧪 专项回归测试已提交 PR #591，CI 尚在 queued，未判定通过。 | #591 | 历史 CI pending/queued；须复验终态 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-12-02` | #12 | 外部工具调用无法绕过网关；生产执行环境有真实文件/网络/进程/凭据隔离验证。 | 状态：⏳ 部分自动化验收已补齐，**本项仍不通过/不勾选**；PR #311 的 Gateway-only 节点契约与负向测试已合并。新增 [PR #602](https://github.com/dujiu1024-dotcom/Agent-pingtai/pull/602)（已合并到 main，HEAD `3e54a902927aeb537772295edf4d487ee263bb3a`）：新增 `tests/test_issue_12_a_12_02_security_acceptance.py`，覆盖缺失  | #311, #602 | 历史 CI pending/queued；须复验终态 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-12-03` | #12 | Runtime 具备取消、超时、运行状态回传和可恢复性；结构化输出失败可控。 | 状态：⏳ 自动化验收用例已补齐，**本项暂不判通过**。PR [#608](https://github.com/dujiu1024-dotcom/Agent-pingtai/pull/608)（已合并到 main，HEAD `89ed0f5e24c5859494d16d509472b5c06c06a916`）新增 `tests/test_issue_12_a_12_03_runtime_acceptance.py` 六项验收：运行中取消并终态收敛、超时持久化 `timed_out`、checkpoint 恢复并回 | #608 | 历史 CI pending/queued；须复验终态 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-12-01` | #12 | 统一 Runtime Adapter 契约、能力声明、版本兼容与模型/Agent 执行生命周期。 | 状态：✅ merged 到 main（PR #389）；实现与测试以该 PR 合并记录为准。 | #389 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-12-02` | #12 | 真实模型 Harness 候选对比 PoC：固定任务集，比较质量、成本、延迟、流式、取消、超时、恢复和工具调用。 | 状态：✅ merged 到 main（PR #349）；基准框架已实现；DeepSeek/Strands 同任务真实调用、代表性质量样本和真实 Token/成本/延迟对比仍待现场验收；本次 PR #341 不覆盖此项。 | #349, #341 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-12-03` | #12 | Gateway-only 工具调用链路；Harness 不得绕过 Tool Gateway。 | 状态：✅ merged 到 main（PR #519）；实现与测试以该 PR 合并记录为准。 | #519 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-12-04` | #12 | 结构化输出 Schema 注册、版本、校验、修复重试和失败处理。 | 状态：✅ merged 到 main（PR #360）；Schema Registry、显式版本和 fail-closed validator 已合并，含边界回归测试。 | #360 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-12-05` | #12 | Agent 子任务委派、委派链、结果回传、循环/深度限制、失败接管。 | 状态：✅ merged 到 main（PR #363）；受监督委派、tenant-bound registry、Child Run 持久化/远端 worker/取消回收已合并。 | #363 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-12-06` | #12 | 多模态输入输出统一接口（图片、PDF、音频、视频），按能力声明启用。 | 状态：✅ merged 到 main（PR #368）；Strands 图片/PDF/音频/视频输入输出、资源授权解析、Artifact Writer 及 Child Run 媒体引用持久化已合并；真实 Provider 实测另列现场验收。 | #368 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-13-01` | #13 | 端到端覆盖分派→并行→提案→证据质疑→冲突裁决→审批→归档。 | 状态：🔄 测试已由 PR [#612](https://github.com/dujiu1024-dotcom/Agent-pingtai/pull/612) 合并进入 main（merge commit `54a998c32d72e968f8d8a50962a0b960d217c70e`）；CI 仍 pending，checkbox 保持未勾选。 | #612 | 历史 CI pending/queued；须复验终态 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-13-02` | #13 | 每项结论可追溯到专家输出、证据和黑板版本。 | 状态：❓ A- 验收任务，需单独核验；测试/验收证据：待补；CI/现场记录：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-13-03` | #13 | 事件至少一次语义下不重复产生外部副作用；保持 Hybrid Blackboard 决策。 | 状态：❓ A- 验收任务，需单独核验；测试/验收证据：待补；CI/现场记录：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-13-01` | #13 | 专家注册/能力描述、任务分派、并行执行、共享上下文和结果汇聚。 | 状态：✅ merged 到 main（PR #371）；代码证据：待补；测试证据：待补；PR/Commit：待补；CI：待补。 | #371 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-13-02` | #13 | Blackboard 事实、主张、证据、质疑、冲突、决策和产物的完整写入/读取 API。 | 状态：✅ merged 到 main（PR #379）；实现与测试以该 PR 合并记录为准。 | #379 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-13-03` | #13 | 证据来源校验、冲突发现、裁决过程与不可变决策记录。 | 状态：✅ merged 到 main（PR #382）；代码证据：待补；测试证据：待补；PR/Commit：待补；CI：待补。 | #382 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-13-04` | #13 | 事件驱动协作、重复事件幂等、运行回放和人工介入。 | 状态：✅ merged 到 main（PR #492）；代码证据：待补；测试证据：待补；PR/Commit：待补；CI：待补。 | #492 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-13-05` | #13 | 任务/项目/租户作用域权限与数据隔离。 | 状态：✅ merged 到 main（PR #491）；代码证据：待补；测试证据：待补；PR/Commit：待补；CI：待补。 | #491 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-14-01` | #14 | 用户可完成创建/配置 Agent、发起任务、查看过程、处理审批、查看结果的主流程。 | 状态：🔨 自动化验收测试已提交 PR [#615](https://github.com/dujiu1024-dotcom/Agent-pingtai/pull/615)（已合并到 main）；分支 `test/issue-14-a-14-01-workbench-journey`，HEAD `2f35d56d5921cb0b81f9e0a0fbb822207f8131f8`。新增 `tests/test_issue_14_a_14_01_workbench_acceptance.py`：通过 Registry  | #615 | 历史 CI pending/queued；须复验终态 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-14-02` | #14 | 管理员可管理模型/工具/知识库权限和版本。 | 状态：❓ A- 验收任务，需单独核验；测试/验收证据：待补；CI/现场记录：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-14-03` | #14 | 新 UI 与旧 FDE UI 解耦，旧入口继续可用。 | 状态：❓ A- 验收任务，需单独核验；测试/验收证据：待补；CI/现场记录：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-14-01` | #14 | 独立于旧 console/gui 的新平台 Web 管理与使用界面。 | 状态：✅ merged 到 main（PR #405）；实现与测试以该 PR 合并记录为准。 | #405 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-14-02` | #14 | 工作台、对话/会话、文件、任务中心、运行详情和人工审批入口。 | 状态：✅ merged 到 main（PR #407）；实现与测试以该 PR 合并记录为准。 | #407 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-14-03` | #14 | Agent/工作流/模型/工具/Skills 管理。 | 状态：✅ merged 到 main（PR #409）；实现与测试以该 PR 合并记录为准。 | #409 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-14-04` | #14 | 知识库、记忆管理入口；运行监控、评测、成本和审计视图。 | 状态：✅ merged 到 main（PR #412）；实现与测试以该 PR 合并记录为准。 | #412 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-14-05` | #14 | 适配桌面与移动窄屏；统一 API 与权限控制，不将业务逻辑写入前端。 | 状态：❌ 未派发，无 PR 记录；代码证据：待补；测试证据：待补；PR/Commit：待补；CI：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-15-01` | #15 | 高风险动作无有效审批必拒绝；审批参数变化后原审批失效。 | 状态：❓ A- 验收任务，需单独核验；审批人授权、自批拒绝、审批参数/Run 绑定、过期/撤销负向测试已存在。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-15-02` | #15 | Harness 不能访问平台数据库、宿主机凭据和非授权文件/网络。 | 状态：❓ A- 验收任务，需单独核验；OCI 命令隔离策略、资源边界负向测试、Gateway 生命周期测试均通过最新 main CI；真实 Docker/Podman daemon 执行、文件/网络/凭据访问、rootless/userns-remap 配置证据和逃逸测试未完成。 | 未记录 | 条目含历史成功记录；须核对最新 HEAD 与覆盖范围 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-15-03` | #15 | 审计记录不可由普通 Agent 修改，失败关闭策略明确。 | 状态：❓ A- 验收任务，需单独核验；PostgreSQL append-only UPDATE/DELETE/TRUNCATE 测试存在；PR #293 已合并（Commit 425f1f61cbd5b67fb9f37a5213571911da4a061f），Security focused、CI、Platform Release Gate 全部通过，补齐扩展 outcome 持久化映射；生产 DB 角色权限与全链路 fail-closed 尚未验证。 | #293 | 条目含历史成功记录；须核对最新 HEAD 与覆盖范围 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-15-01` | #15 | 将身份/资源授权与 Tool Gateway、MCP、Harness、知识检索、文件访问统一集成。 | 状态：✅ merged 到 main（PR #361）；PR #273 已强制 Registry 工具提供资源 ACL；PR #284 又将强制检查扩展到 SupervisedToolGateway 直接构造入口，并在每次调用中由可信 resolver 重算 resource、拒绝 caller resource spoofing。P0 复核：知识检索由 `KnowledgeService.retrieve` 委托 `PostgresKnowledgeRepository.search`，PostgreSQL FT | #361, #273, #284 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-15-02` | #15 | 审批工作台、策略配置、审批人授权、过期/撤销、参数摘要绑定和审批留痕。 | 状态：✅ merged 到 main（PR #364）；审批服务/API/UI、服务端审批人授权、参数摘要绑定（actor/resource/run/args）、过期/撤销及申请人自批拒绝均有实现和回归测试；生产策略变更演练另列现场验收。 | #364 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-15-03` | #15 | 平台级审计覆盖登录、配置变更、数据访问、Agent 运行、模型调用、工具副作用、审批和管理员操作。 | 状态：✅ merged 到 main（PR #367）；append-only 审计、租户查询 API、HTTP 请求意图/结果审计已合并；PR #284 覆盖 Webhook Delivery Worker，PR #286 覆盖 Outbox Relay，PR #288 覆盖 durable AlertNotificationDispatcher，PR #289 覆盖 MemoryExtractionEventHandler，均加入 system actor write-ahead audit、结果审计及审计故障 | #367, #284, #286, #288, #289, #293, #297, #300, #316 | 历史 CI 有失败/取消记录；须按最新 PR HEAD 复验 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-15-04` | #15 | 执行沙箱隔离文件系统、进程、网络、密钥、资源配额；默认拒绝网络与宿主机访问。 | 状态：✅ merged 到 main（PR #369）；PR #291 已合并（Commit a7818c9），最新 head 的 Security focused、Python 3.11/3.12、Postgres/Redis integration、Platform Release Gate 全部通过。OCI runner 具备 digest pin、network none、只读 rootfs、cap drop、non-root、private namespace（Podman）、Docker rootles | #369, #291 | 条目含历史成功记录；须核对最新 HEAD 与覆盖范围 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-15-05` | #15 | 密钥托管、轮换、脱敏、日志防泄漏和供应链安全。 | 状态：✅ merged 到 main（PR #387）；SecretManager 版本生命周期及 Vault KV v2 backend 已实现，FakeVault 生命周期/失败关闭测试通过；生产 Vault/KMS 接线、权限策略与轮换审计另列现场验收。 | #387 | 历史 CI 有失败/取消记录；须按最新 PR HEAD 复验 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-16-01` | #16 | 功能与契约测试：对本 Issue 每项编码任务提供对应单元/API/契约测试，覆盖正常、边界和失败路径。 | 状态：❓ A- 验收任务，需单独核验；测试证据：Trace、Prometheus、告警、评测、Prompt 和人工评分均有专项测试；PR #269 新增平台应用组合测试；PR #277 新增告警规则 Worker 持久化失败重试、持久化去重、轮询失败恢复及应用生命周期测试；CI：#269 检查已通过；#277 security-focused 已通过，其余 Python 3.11/3.12、身份、PostgreSQL/Redis、E2E 检查仍运行中。真实 Langfuse/Provider 端到端契约尚无现场证据 | #269, #277 | 历史 CI 有失败/取消记录；须按最新 PR HEAD 复验 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-16-02` | #16 | 安全与可靠性测试：覆盖租户隔离、权限拒绝、幂等/重试/并发及依赖不可用等适用场景；有数据库依赖时提供集成测试。 | 状态：⏳ 已完成既有实现与自动化测试复核，基础验收项通过；**专项 PostgreSQL Observability 集成测试仍待本轮 CI 执行成功，故暂不勾选通过**。 | #269, #277, #661 | 历史 CI 有失败/取消记录；须按最新 PR HEAD 复验 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-16-03` | #16 | 交付与运行验收：最新 main CI 通过，文档/迁移/配置齐全；需真实服务或生产环境验证的项目单独列现场证据，不以 mock 代替。 | 状态：⏳ 已完成交付清单、文档/迁移/配置盘点并提交验收 PR；**尚未通过最终验收**。 | #670, #269 | 历史 CI 有失败/取消记录；须按最新 PR HEAD 复验 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-16-01` | #16 | 跨组件 Trace/Span：关联请求、Run、Agent、模型、Prompt、工具、知识检索和租户；敏感字段脱敏。 | 状态：✅ merged 到 main（PR #391）；代码证据：`agent_platform/observability/trace_context.py`、`trace_middleware.py`、`harness.py`、`agent_platform/models/runtime_router.py`、`agent_platform/tools/gateway.py`、`gateway_mcp.py`、`agent_platform/knowledge/rag.py`；测试证据：`tests/test_ | #391 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-16-02` | #16 | 指标与日志：Token/成本/时延/成功率/拒绝率/重试/工具失败/队列积压等低基数指标及结构化日志。 | 状态：✅ merged 到 main（PR #338）；代码证据：`agent_platform/observability/metrics.py`、`prometheus.py`、`harness.py`、`agent_platform/models/runtime_router.py`、`agent_platform/tools/gateway.py`、`agent_platform/knowledge/rag.py`；测试证据：`tests/test_observability.py`、`test_obser | #338 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-16-03` | #16 | 告警规则与通知运营：阈值、抑制/聚合、Outbox、Provider、重试、值班/确认/解决闭环。 | 状态：✅ merged 到 main（PR #461）；实现与测试以该 PR 合并记录为准。 | #461 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-16-04` | #16 | Agent/RAG 评测数据集与运行编排：固定版本、并发限制、模型端点、检索/回答/引用质量指标。 | 状态：✅ merged 到 main（PR #318）；代码证据：`agent_platform/observability/evaluation.py`、`evaluation_runner.py`、`evaluation_store.py`、`tests/fixtures/knowledge_rag_eval_v1.json`；测试证据：`tests/test_evaluation_runner.py`、`test_evaluation_store.py`、`test_knowledge_memory_api. | #318 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-16-05` | #16 | 人工评分、回归对比和发布门禁：评分权限/脱敏、报告持久化、阈值策略与可追溯证据。 | 状态：✅ merged 到 main（PR #318）；代码证据：`agent_platform/observability/evaluation_store.py`、`agent_platform/api/observability.py`、`agent_platform/api/app.py`；测试证据：`tests/test_evaluation_store.py`、`test_observability_alerts.py`、`test_platform_api_app.py`；PR/Commit：#134 | #318 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-16-06` | #16 | Prompt 注册与运营：版本、变量校验、测试、灰度/回滚和变更审计；与评测门禁协同。 | 状态：✅ merged 到 main（PR #306）；代码证据：`agent_platform/observability/prompts.py`、`prompt_store.py`、`agent_platform/api/prompts.py`、`agent_platform/observability/prompt_adapter.py`、`agent_platform/api/app.py`、`agent_platform/storage/postgres/017_prompt_registry.sql`、 | #306, #499 | 历史 CI pending/queued；须复验终态 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-17-01` | #17 | 功能与契约测试：对本 Issue 每项编码任务提供对应单元/API/契约测试，覆盖正常、边界和失败路径。 | 状态：❓ A- 验收任务，需单独核验；测试证据：配额预算边界、原子并发、模型预留/结算、Harness 计量、API 导出与成本对账均有专项测试；PR：#219、#222、#231、#250、#257；CI：相关合并检查通过。真实多实例工具/存储计量链路仍待验收。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-17-02` | #17 | 安全与可靠性测试：覆盖租户隔离、权限拒绝、幂等/重试/并发及依赖不可用等适用场景；有数据库依赖时提供集成测试。 | 状态：🟡 已提交专项测试 PR，等待 CI 运行结果，尚未最终验收。 | #673 | 历史 CI pending/queued；须复验终态 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-17-03` | #17 | 交付与运行验收：最新 main CI 通过，文档/迁移/配置齐全；需真实服务或生产环境验证的项目单独列现场证据，不以 mock 代替。 | 状态：🟡 交付清单与验收记录已提交 PR #675，CI pending；发现生产组合缺口，不能判定验收通过。 | #675 | 历史 CI 有失败/取消记录；须按最新 PR HEAD 复验 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-17-01` | #17 | 租户/项目/主体级配额模型：资源维度、策略优先级、默认值、覆盖关系与配置审计。 | 状态：✅ merged 到 main（PR #482）；代码证据：`agent_platform/quotas/service.py`、`postgres.py`、`agent_platform/api/quotas.py`、`agent_platform/storage/postgres/016_tenant_quotas_cost.sql`、`030_quota_reserve_null_safety.sql`、`036_quota_split_token_budgets.sql`；测试证据：`tests/te | #482 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-17-02` | #17 | 并发与速率治理：原子预留/释放、并发上限、排队/拒绝策略、租户公平性及过期预留回收。 | 状态：✅ merged 到 main（PR #319）；代码证据：`agent_platform/quotas/concurrency.py`、`agent_platform/runtime/workbench_tasks.py`、`agent_platform/models/runtime_router.py`、`agent_platform/storage/postgres/038_queue_worker_concurrency_quotas.sql`；测试证据：`tests/test_quota_concu | #319 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-17-03` | #17 | 成本计量与报表：模型/工具/存储等用量、价格目录版本、归属维度、估算与实际差异。 | 状态：✅ merged 到 main（PR #321）；代码证据：`agent_platform/quotas/pricing.py`、`storage_meter.py`、`harness.py`、`postgres.py`、`agent_platform/models/runtime_router.py`；测试证据：`tests/test_quota_pricing_catalog.py`、`test_quota_pricing_runtime_adapter.py`、`test_quota_storage_m | #321 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-17-04` | #17 | 管理 API 与运行时集成：配额查询/变更、超限告警、审计和 fail-closed 行为；单 Run Token/context-window 预算由 #72 负责。 | 状态：✅ merged 到 main（PR #423）；代码证据：`agent_platform/api/quotas.py`、`agent_platform/quotas/harness.py`、`agent_platform/runtime/harness_factory.py`、`agent_platform/models/runtime_router.py`、`agent_platform/runtime/workbench_tasks.py`；测试证据：`tests/test_quota_reportin | #423 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-18-01` | #18 | 可从空环境按文档部署；升级失败可回滚。 | 状态：❓ A- 验收任务，需单独核验；测试/验收证据：待补；CI/现场记录：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-18-02` | #18 | 完成备份恢复演练并记录实际 RPO/RTO。 | 状态：❓ A- 验收任务，需单独核验；测试/验收证据：待补；CI/现场记录：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-18-03` | #18 | Redis 丢失后可由 PostgreSQL Outbox 重放；业务事实不依赖 Redis 唯一保存。 | 状态：❓ A- 验收任务，需单独核验；测试/验收证据：待补；CI/现场记录：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-18-01` | #18 | 开发/测试/生产环境配置和部署基线，容器化及环境变量/密钥管理。 | 状态：✅ merged 到 main（PR #511）；实现与测试以该 PR 合并记录为准。 | #511 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-18-02` | #18 | 版本发布与升级：迁移前检查、兼容窗口、滚动升级、版本回退和配置回滚。 | 状态：✅ merged 到 main（PR #334）；实现与测试以该 PR 合并记录为准。 | #334 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-18-03` | #18 | PostgreSQL、对象存储、配置和必要运行状态的备份策略。 | 状态：✅ merged 到 main（PR #334）；代码证据：待补；测试证据：待补；PR/Commit：待补；CI：待补。 | #334 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-18-04` | #18 | 恢复演练、RPO/RTO 目标、灾难恢复和故障手册。 | 状态：✅ merged 到 main（PR #336）；实现与测试以该 PR 合并记录为准。 | #336 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-18-05` | #18 | 监控基础设施、队列、存储、数据库容量和证书到期。 | 状态：✅ merged 到 main（PR #422）；代码证据：待补；测试证据：待补；PR/Commit：待补；CI：待补。 | #422 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-19-01` | #19 | 不出现双主写；每个数据对象有来源、目标、映射和迁移责任。 | 状态：❓ A- 验收任务，需单独核验；测试/验收证据：待补；CI/现场记录：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-19-02` | #19 | 迁移可重复执行、可校验、可回滚；旧系统继续运行直到切换验收。 | 状态：❓ A- 验收任务，需单独核验；测试/验收证据：待补；CI/现场记录：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-19-03` | #19 | 删除/导出流程覆盖主库、对象存储、向量索引、缓存和连接器副本。 | 状态：❓ A- 验收任务，需单独核验；测试/验收证据：待补；CI/现场记录：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-19-01` | #19 | 盘点旧 agency.py、db.py、memory.py、vectors.py、conversation.py、gui.py、飞书接入等资产，逐项决定复用/适配/替换/退役。 | 状态：✅ merged 到 main（PR #340）；代码证据：待补；测试证据：待补；PR/Commit：待补；CI：待补。 | #340 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-19-02` | #19 | 迁移批次与工具：抽取、转换、校验、断点续传、幂等、差异报告和批次状态。 | 状态：✅ merged 到 main（PR #345）；代码证据：`agent_platform/data_governance/migration_batches.py`（只读 SQLite 抽取、显式列映射/白名单转换、批次状态、单列主键 keyset checkpoint、目标 UPSERT、逐行幂等账本、源库指纹、差异统计）；`scripts/run_legacy_migration.py`（run/status/diff CLI）；文档：`docs/data-governance/migration-ba | #345, #343 | 条目含历史成功记录；须核对最新 HEAD 与覆盖范围 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-19-03` | #19 | 设计数据保留、归档、删除、导出、脱敏、审计和法律保留策略。 | 状态：✅ merged 到 main（PR #347）；代码证据：待补；测试证据：待补；PR/Commit：待补；CI：待补。 | #347 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-19-04` | #19 | 定义用户/租户删除时关联会话、文件、记忆、向量、日志和缓存的级联处理。 | 状态：✅ merged 到 main（PR #425）；实现与测试以该 PR 合并记录为准。 | #425 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-19-05` | #19 | 旧 SQLite 到 PostgreSQL 的迁移校验、差异报告与切换门禁。 | 状态：✅ merged 到 main（PR #359）；代码证据：待补；测试证据：待补；PR/Commit：待补；CI：待补。 | #359 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-20-01` | #20 | API 契约测试覆盖版本、401/403/429、分页、幂等和稳定错误码。 | 状态：❓ A- 验收任务，需单独核验；对应最新工作流仍 queued，待 CI 完成后复验。 | 未记录 | 历史 CI pending/queued；须复验终态 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-20-02` | #20 | Webhook 覆盖签名有效/过期/篡改、重试、死信、重放授权和租户隔离。 | 状态：❓ A- 验收任务，需单独核验；对应最新工作流仍 queued，待 CI 完成后复验。 | 未记录 | 历史 CI pending/queued；须复验终态 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-20-03` | #20 | 生产运行验收：真实 Redis/PostgreSQL/Secret Manager、Worker 启停、回调投递及审计链路。 | 状态：❓ A- 验收任务，需单独核验；尚无本轮生产现场证据。 | 未记录 | 历史 CI 有失败/取消记录；须按最新 PR HEAD 复验 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-20-01` | #20 | 对外 API 版本兼容、认证授权、限流、幂等、分页、错误码和契约文档。 | 状态：✅ merged 到 main（PR #427）；契约测试 #436；401/403/429 SDK 与 HTTP 边界测试 #444；SDK API 版本头修正 #451；稳定 HTTP 错误码扩展 #452。最新补充 PR #452 已合并，merge commit：`e4ec34828e15a575d0a0a42d5eeace482d9d39b4`。相关 PR 的 CI、Security、Dependency Audit、Release Gate 当前仍显示 queued，不能标记 CI 全绿。 | #427, #452 | 历史 CI pending/queued；须复验终态 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-20-02` | #20 | Webhook 订阅管理、签名、投递、去重、重试、死信、授权重放和审计。 | 状态：✅ merged 到 main（PR #428）；签名时间窗修正 #428；Lease fencing / 审计加固 #438；签名过期边界测试 #445；签名输入格式及密钥强度硬化 #453。最新补充 PR #453 已合并，merge commit：`254cf7661abc540e0970c4419f8319bd1da4714a`。相关 PR 的 CI、Security、Dependency Audit、Release Gate 当前仍显示 queued，不能标记 CI 全绿。 | #428, #453 | 历史 CI pending/queued；须复验终态 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-20-03` | #20 | Python SDK/示例客户端：认证、超时、重试、类型、错误处理和版本兼容。 | 状态：✅ merged 到 main（PR #467）；PR #431、#451；全量集成测试仍受历史 PostgreSQL 迁移编号重复阻塞（038/039/040 等），需治理后复验。 | #467, #431 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-20-04` | #20 | 扩展/插件生命周期、依赖锁定、权限声明、失败补偿和可信发布者管理。 | 状态：✅ merged 到 main（PR #469）；可信发布者注册表、PostgreSQL 持久化状态、租户隔离密钥注册/轮换/吊销 API 和迁移 075 已进入 main，merge commit：`14d6b15428054990938a4a8416cca8e5f9f82904`；补充 PostgreSQL 持久化事务测试 PR #450 尚待 CI。 | #469, #450 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-20-05` | #20 | 国际化与区域配置：语言、时区、日期/数字格式和租户区域偏好。 | 状态：✅ merged 到 main（PR #470） | #470 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-21-01` | #21 | 核心业务链路、关键安全负向场景和恢复场景自动化可重复执行。 | 状态：✅ merged 到 main（PR #483）；测试/验收证据：待补；CI/现场记录：待补。 | #483 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-21-02` | #21 | 发布门禁可阻断关键质量/安全回归。 | 状态：❓ A- 验收任务，需单独核验；测试/验收证据：待补；CI/现场记录：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-21-03` | #21 | 所有生产验收项有证据、责任人和结论。 | 状态：❓ A- 验收任务，需单独核验；测试/验收证据：待补；CI/现场记录：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-21-01` | #21 | 建立从登录/授权→配置 Agent→知识检索→多专家执行→人工审批→产物交付的端到端测试。 | 状态：✅ merged 到 main（PR #439）；代码证据：待补；测试证据：待补；PR/Commit：待补；CI：待补。 | #439 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-21-02` | #21 | 覆盖租户隔离、越权拒绝、Prompt 注入、工具滥用、数据泄露和沙箱逃逸测试。 | 状态：✅ merged 到 main（PR #440）；代码证据：待补；测试证据：待补；PR/Commit：待补；CI：待补。 | #440 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-21-03` | #21 | 覆盖服务重启、Worker 崩溃、Redis 中断、数据库故障、模型超时、重复事件和恢复流程。 | 状态：✅ merged 到 main（PR #441）；代码证据：复用 `agent_platform/runtime/workbench_tasks.py`、`agent_platform/runtime/postgres_lifecycle.py`、`agent_platform/events/redis_streams.py`、`agent_platform/events/consumer.py`、`agent_platform/events/receipts.py`；测试证据：新增 `tests/test_ | #441, #437 | 历史 CI pending/queued；须复验终态 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-21-04` | #21 | 定义性能/并发/成本/质量基线和发布门禁。 | 状态：✅ merged 到 main（PR #481）；代码证据：待补；测试证据：待补；PR/Commit：待补；CI：待补。 | #481 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-21-05` | #21 | 建立部署前检查、迁移检查、回滚检查和生产验收报告模板。 | 状态：✅ merged 到 main（PR #473）；代码证据：复用 `scripts/release_gate.py` 与 `docs/release/RELEASE_EVIDENCE_TEMPLATE.json`，新增 `scripts/release_acceptance_report.py`（离线生成含九类检查、负责人、UTC 时间、证据引用、部署预检、制品 SHA-256、迁移兼容、回滚演练、阻断项和签核的 Markdown 验收报告；证据校验不通过强制 NO-GO，并输出非零退出码），新增 `docs | #473, #500 | 历史 CI 有失败/取消记录；须按最新 PR HEAD 复验 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-22-01` | #22 | 从真实知识库样本抽取的实体/关系有 ≥80% 可追溯到源文档片段，置信度与候选确认链路完整。 | - 状态：❓ A- 验收任务，需单独核验；测试/验收证据：待补；CI/现场记录：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-22-02` | #22 | 关系查询（路径、邻居、子图）在典型规模下返回 ≤500ms，权限隔离不漏不越。 | - 状态：🧪 验收脚本已提交 PR #662（已合并到 main）；CI 已触发，当前 queued；PostgreSQL 基准依赖 `AGENT_PLATFORM_TEST_DSN`，尚未取得实际延迟数据，因此暂不勾选通过。 | #662 | 历史 CI pending/queued；须复验终态 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-22-03` | #22 | 混合检索（图 + 向量）在关系型问题上的答案质量显著优于纯向量（≥20% 人工评分提升或等价自动指标）。 | - 状态：🧪 自动化评测已提交 PR #668（已合并到 main）；CI queued，未取得执行结果；本项暂不标记通过。 | #668 | 历史 CI pending/queued；须复验终态 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-22-04` | #22 | 删除传播覆盖图谱记录、索引、缓存和并发写入竞态，最终一致。 | - 状态：🧪 应用层删除失效 worker 与并发/重试测试已提交 PR #669（已合并到 main）；CI 已触发，当前 queued/pending，未取得测试结果；暂不标记通过。 | #669 | 历史 CI 有失败/取消记录；须按最新 PR HEAD 复验 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-22-05` | #22 | 图谱 Schema 与存储选型有生产环境性能基准（并发写入、查询延迟、内存占用）。 | - 状态：❓ A- 验收任务，需单独核验；测试/验收证据：待补；CI/现场记录：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-22-01` | #22 | 图谱 Schema 定义：实体类型、关系类型、属性约束，与 #10 知识库文档、#11 记忆条目、#13 Blackboard 消息的映射规则；支持运行时扩展新实体/关系类型。 | - 状态：⏳ 未派发，无 PR 记录 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-22-02` | #22 | 图谱存储与索引：图存储选型（Neo4j / Neptune / LightGraph / 嵌入式）、索引策略（实体名、类型、来源引用、租户/项目权限）、增量写入与异步索引；复用 #11 租户/项目隔离模型。 | - 状态：⏳ 未派发，无 PR 记录 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-22-03` | #22 | 图谱构建管道：从知识库文档（#10）、记忆提取（#11）、Blackboard 事件（#13）、文件资产（#8）自动抽取实体和关系；抽取契约、置信度、候选确认、冲突处理、版本与来源引用；复用 #11 MemoryCandidate 工作流。 | - 状态：⏳ 未派发，无 PR 记录 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-22-04` | #22 | 图谱查询与检索 API：图查询 DSL 或模板（路径查询、邻居扩展、子图匹配、最短路径）、与 RAG 混合检索（先图缩小范围、再向量重排）、租户/项目权限过滤、超时/截断/去重。 | - 状态：⏳ 未派发，无 PR 记录 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-22-05` | #22 | 图谱治理：删除传播（实体删除 → 关联关系 → 派生索引/缓存）、过期与归档、纠错与合并、版本溯源、审计日志；复用 #11 删除传播与租户授权模型。 | - 状态：⏳ 未派发，无 PR 记录 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-22-06` | #22 | 图谱可视化与运营接口（生产化阶段）：实体/关系浏览、图谱质量指标（孤立节点占比、关系密度、活跃子图）、人工标注纠正入口。 | - 状态：⏳ 未派发，无 PR 记录 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-23-01` | #23 | A/B 测试评分稳定（方差 < 5%） | - 状态：❓ A- 验收任务，需单独核验；测试/验收证据：待补；CI/现场记录：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-23-02` | #23 | 灰度切分不影响其他 variant，路由决策可审计 | - 状态：❓ A- 验收任务，需单独核验；测试/验收证据：待补；CI/现场记录：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-23-03` | #23 | 版本回滚 30s 内生效 | - 状态：❓ A- 验收任务，需单独核验；测试/验收证据：待补；CI/现场记录：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-23-01` | #23 | Prompt 版本与 variant 数据模型：version/variant 追溯、变更审计、废弃回滚 | - 状态：🔨 实现已提交，PR #573（内存审计/回滚）+ PR #577（注册输入类型校验补强）均 已合并到 main；原始模型 PR #538、adapter 保护 PR #571 已合并。 | #573, #577, #538, #571 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-23-02` | #23 | A/B 测试引擎：测试集定义、自动打分、对比矩阵、LLM-as-judge | - 状态：⏳ 未派发，无 PR 记录 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-23-03` | #23 | 灰度发布与流量切分：按百分比/标签/租户路由、手动/自动切流、审计 | - 状态：⏳ 未派发，无 PR 记录 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-23-04` | #23 | Prompt 版本管理 API + #14 工作台集成 | - 状态：⏳ 未派发，无 PR 记录 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-24-01` | #24 | 端到端 RAG 管线跑通，chunk 可追溯原文 | - 状态：🔨 自动化验收测试已提交 PR [#620](https://github.com/dujiu1024-dotcom/Agent-pingtai/pull/620)（已合并到 main）；分支：`test/issue-24-a-24-01-rag-e2e`；head：`4cadc6aeda8777ab0f269754942808f2a5d556cf`。 | #620, #590 | 历史 CI pending/queued；须复验终态 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-24-02` | #24 | 质量指标可重复（同数据集/模型/超参） | - 状态：🔨 自动化验收测试已提交 PR [#623](https://github.com/dujiu1024-dotcom/Agent-pingtai/pull/623)（已合并到 main）；分支：`test/issue-24-a-24-02-quality-reproducibility`；HEAD：`dad9f57cc94e5e65578ae3860aa4217260b6fae2`。 | #623 | 历史 CI pending/queued；须复验终态 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-24-01` | #24 | 数据集上传与索引管道：PDF/MD/HTML → chunk → embedding → 入库，复用 knowledge/ | - 状态：🔨 已实现并提交 PR，等待 CI；本地 pytest/Ruff 未执行（当前执行环境无仓库 checkout），不提前计为自动化通过。 | #590 | 历史 CI pending/queued；须复验终态 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-24-02` | #24 | Chunk 预览界面：原文 + token 数 + 页码/位置 + 手动编辑 | - 状态：⏳ 未派发，无 PR 记录 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-24-03` | #24 | Query 测试界面：输入问题 → Top-K 召回 → 分数高亮 → 答案生成 → 切 embedding/rerank | - 状态：⏳ 未派发，无 PR 记录 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-24-04` | #24 | RAG 质量指标：Hit Rate@K、MRR、NDCG、Token 覆盖比 | - 状态：⏳ 未派发，无 PR 记录 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-25-01` | #25 | Run 中途 kill → 重开 → 无缝衔接，token 计数正确 | - 状态：🧪 自动化验收测试已提交并修订 PR [#627](https://github.com/dujiu1024-dotcom/Agent-pingtai/pull/627)（已合并到 main）；分支：`test/issue-25-a-25-01-kill-restart-token-accounting`；HEAD：`801f3cdfd97d470b54712165da8500e3bc9b849a`。 | #627 | 历史 CI 有失败/取消记录；须按最新 PR HEAD 复验 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-25-02` | #25 | 删除传播 + 租户隔离不漏 | - 状态：🧪 已实现并提交验收 PR [#634](https://github.com/dujiu1024-dotcom/Agent-pingtai/pull/634)（已合并到 main）；分支：`test/issue-25-a-25-02-delete-propagation-tenant-isolation`；HEAD：`0d13534bbbcec447294cff6875c1feafc850bd37`。 | #634 | 历史 CI 有失败/取消记录；须按最新 PR HEAD 复验 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-25-01` | #25 | Session/Conversation 数据模型：messages + context_window_snapshot + 隔离模型 | - 状态：⏳ 未派发，无 PR 记录 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-25-02` | #25 | 消息流持久化与恢复：Run 开始拉历史、过程追加 + checkpoint、断点恢复 | - 状态：⏳ 未派发，无 PR 记录 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-25-03` | #25 | Session 管理 API：list/get/delete/export + 归档/恢复 + 权限 | - 状态：⏳ 未派发，无 PR 记录 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-25-04` | #25 | Session 与 #11 记忆边界：短期对话 → 长期沉淀的写入时机 | - 状态：⏳ 未派发，无 PR 记录 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-26-01` | #26 | 同一 eval 跑两次差异 < 5% | - 状态：❓ A- 验收任务，需单独核验；测试/验收证据：待补；CI/现场记录：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-26-02` | #26 | 成本/tokens/延迟统计误差 < 1% | - 状态：❓ A- 验收任务，需单独核验；测试/验收证据：待补；CI/现场记录：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-26-01` | #26 | Eval 数据集管理：输入 → 预期输出/rubric，CSV/JSONL 导入，版本 + 隔离 | - 状态：⏳ 未派发，无 PR 记录 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-26-02` | #26 | Eval Runner：Prompt + Dataset → 多模型 variant → 自动打分 → 成本/tokens/延迟 | - 状态：🔨 已实现并提交 PR，等待 CI；本地 pytest/Ruff 未执行（当前 connector-only 执行环境无仓库 checkout），不提前计为自动化通过。 | #585 | 历史 CI pending/queued；须复验终态 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-26-03` | #26 | 对比与报告：variant 矩阵 + 最佳推荐 + 导出 PDF/HTML | - 状态：🔨 实现已提交，PR #586 已合并到 main；CI 最新 head 的 7 个工作流（ci、security-focused、platform-release-gate、dependency-audit、dependency-security、quality-capacity-baseline、harness-benchmark-poc）均 queued/pending。 | #586 | 历史 CI pending/queued；须复验终态 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-26-04` | #26 | 自动切流：结论推送 #23 Prompt Lab 灰度路由（可选） | - 状态：🔨 核心实现由 PR #595 合并；认证身份读取兼容修复由 PR #598 提交（已合并到 main）；不将初始 PR 合并等同于 CI/现场验收完成。 | #595, #598 | 历史 CI 有失败/取消记录；须按最新 PR HEAD 复验 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-27-01` | #27 | 凭据加密 + 运行时解密审计 + 删除传播 | - 状态：🧪 自动化验收测试已提交 PR #606（已合并到 main）；CI 尚未触发/待运行；真实 Vault/KMS 加密配置及生产密钥策略仍待现场验证，因此不勾选为验收通过。 | #606 | 历史 CI 有失败/取消记录；须按最新 PR HEAD 复验 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-27-02` | #27 | 新 connector 不影响现有 connector 运行 | - 状态：❓ A- 验收任务，需单独核验；测试/验收证据：待补；CI/现场记录：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-27-01` | #27 | 凭据/Secret Store：OAuth/API Key/Service Account 加密存 + scope 标签 + 复用，复用 #15 安全 | - 状态：🔀 已合并 main（PR #579，merge commit `50b0568afbb082ffa161dd257dd1f31ce2bdd03e`）；实现与代码核验完成，CI 仍 pending，暂不计入“CI 通过”的完成统计。 | #579 | 历史 CI 有失败/取消记录；须按最新 PR HEAD 复验 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-27-02` | #27 | Connector Catalog：manifest + registry + 健康检查 + 按需加载 | - 状态：🔨 实现已提交，PR #583 已合并到 main；仅 C-27-02。PR 未合并前保留未勾选状态。 | #583 | 历史 CI 有失败/取消记录；须按最新 PR HEAD 复验 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-27-03` | #27 | Connector 框架：BaseConnector 接口 + 鉴权标准流程 + 迁移现有 3 个 connector | - 状态：🔨 实现已提交，PR #588 已合并到 main；PR 以 C-27-02 分支为 base（依赖 PR #583），避免重复包含 C-27-02；未合并前保持未勾选。 | #588, #583 | 历史 CI 有失败/取消记录；须按最新 PR HEAD 复验 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-28-01` | #28 | 安装执行卸载全生命周期正常，无残留副作用 | - 状态：🔨 验收测试已提交 PR #607（已合并到 main）；HEAD `c7fc09f4c5d1a7cacf526b32ace03a82ee3cf05c`。新增 `tests/test_issue_28_a_28_01_skill_lifecycle_acceptance.py`，集成验证 SkillPackageRegistry 发布→Agent 安装→SkillSandboxRuntime 隔离加载→卸载；断言 Agent 安装记录、沙箱副本与 Agent runtime 映射清理，卸载后不可加载，且 | #607 | 历史 CI pending/queued；须复验终态 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-28-02` | #28 | 恶意 Skill 被扫描拦截，无法安装 | - 状态：❓ A- 验收任务，需单独核验；测试/验收证据：待补；CI/现场记录：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-28-01` | #28 | Skill Registry：manifest.yaml + 上传/验依赖/发布/版本 + Agent 级安装，复用 #18 容器注册 | - 状态：⏳ 未派发，无 PR 记录 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-28-02` | #28 | Skill 执行沙箱：OCI 容器 + 生命周期 + 资源隔离 + 安全扫描，复用 #12 sandbox/oci.py | - 状态：⏳ 未派发，无 PR 记录 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-28-03` | #28 | Skill API：list/install/uninstall/upgrade/status + #14 工作台集成 | - 状态：⏳ 未派发，无 PR 记录 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-29-01` | #29 | Replay 上下文与原 Run 一致，回放一致率 ≥ 95% | - 状态：❓ A- 验收任务，需单独核验；测试/验收证据：待补；CI/现场记录：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-29-02` | #29 | Replay 可中途暂停修改后继续 | - 状态：❓ A- 验收任务，需单独核验；测试/验收证据：待补；CI/现场记录：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-29-01` | #29 | Run Trace 事件采集：emit 标准化事件流（RUN_START/STEP/LLM/TOOL/ERROR/COMPLETE）+ 落库，复用 #13 Blackboard 事件 | - 状态：⏳ 未派发，无 PR 记录 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-29-02` | #29 | Replay API：从持久化数据重建上下文（不硬编码默认值）→ 逐步回放 → 停/快进/回退/重跑 | - 状态：✅ 基础实现已合并（PR #574）；本次仅做该项 API 输入校验加固，新增 PR #593；创建后 GitHub 状态已变为 merged（非本次执行主动调用合并操作）。 | #574, #593 | 历史 CI pending/queued；须复验终态 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-29-03` | #29 | Replay UI：Run 时间线 + Blackboard 快照 + Agent 消息流转 + #14 工作台集成 | - 实现：PR [#580](https://github.com/dujiu1024-dotcom/Agent-pingtai/pull/580) 已合并，提交 `57ef94d99e17306f4e317a1575dd6c8ca9617d5d`；`agent_platform/web/static/index.html` 增加工作台 Run 回放导航，`agent_platform/web/static/app.js` 实现持久化 Run 时间线、重建上下文、Blackboard Run 范围条目/快照、Age | #580, #597 | 历史 CI pending/queued；须复验终态 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-30-01` | #30 | token 延迟 P50 < 100ms，断线重连不丢失 | - 状态：❓ A- 验收任务，需单独核验；测试/验收证据：待补；CI/现场记录：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-30-02` | #30 | 流式/非流式输出内容一致 | - 状态：❓ A- 验收任务，需单独核验；测试/验收证据：待补；CI/现场记录：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-30-01` | #30 | SSE 协议层：统一事件格式（token_stream/step_progress/tool_output/run_complete/error）+ 心跳 + 重连 + 客户端库 | - 状态：⏳ 未派发，无 PR 记录 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-30-02` | #30 | Agent 执行流式改造：LLM stream=true → 逐 token emit → 前端实时渲染 + Step/Tool 事件，对齐 #29 事件模型 | - 状态：⏳ 未派发，无 PR 记录 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-30-03` | #30 | 与 #13 Blackboard 集成：Blackboard 状态变更通过 SSE 推送到订阅客户端 | - 状态：🔄 实现已提交并合并（PR #576；merge commit `6f8e0d89b0527799626a4cfe3edf941c1c85ddaf`）；自动化完成状态暂不确认，CI 仍 pending。 | #576 | 历史 CI pending/queued；须复验终态 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-31-01` | #31 | 并发 N 下 100 任务完成时间 ≤ 单任务 × ceil(100/N) × 2 | - 状态：❓ A- 验收任务，需单独核验；测试/验收证据：待补；CI/现场记录：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-31-02` | #31 | 单任务 OOM/超时不影响其他，批量可继续 | - 状态：❓ A- 验收任务，需单独核验；测试/验收证据：待补；CI/现场记录：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-31-01` | #31 | 批量执行器：模板 + 输入数据集 + 并发控制（复用 quotas/concurrency）+ 错误隔离 + 结果收集 | - 状态：⏳ 未派发，无 PR 记录 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-31-02` | #31 | 批量结果与报告：汇总表（通过率/分数/成本/延迟）+ 逐任务详情 + CSV/JSON 导出，对齐 #26 报告格式 | - 状态：⏳ 未派发，无 PR 记录 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-31-03` | #31 | 批量 API：submit/query/cancel/retry + #14 工作台集成 | - 状态：⏳ 未派发，无 PR 记录 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-72-01` | #72 | 所有平台模型调用都经过统一预算检查；没有预算配置时采用明确安全默认值。 | 状态：❓ A- 验收任务，需单独核验；测试/验收证据：待补；CI/现场记录：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-72-02` | #72 | 并发调用不会超额透支；失败和重试的扣减语义一致。 | 状态：❓ A- 验收任务，需单独核验；测试/验收证据：待补；CI/现场记录：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `A-72-03` | #72 | 运行详情和审计记录可查询预算与实际用量。 | 状态：❓ A- 验收任务，需单独核验；测试/验收证据：待补；CI/现场记录：待补。 | 未记录 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-72-01` | #72 | 定义按 tenant/project/Agent/Run 可配置的 input、context-window、output token 和总成本预算。 | 状态：✅ merged 到 main（PR #338）；代码证据：待补；测试证据：待补；PR/Commit：待补；CI：待补。 | #338 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-72-02` | #72 | 在模型调用前统一估算/计算 token；预留安全余量，避免超出模型上下文窗口。 | 状态：✅ merged 到 main（PR #339）；代码证据：待补；测试证据：待补；PR/Commit：待补；CI：待补。 | #339 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-72-03` | #72 | 定义超限策略：拒绝、截断、摘要压缩或人工介入；策略必须可审计、可测试，不得静默丢弃关键上下文。 | 状态：✅ merged 到 main（PR #346）；代码证据：待补；测试证据：待补；PR/Commit：待补；CI：待补。 | #346 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-72-04` | #72 | Run/模型调用持久化 token 用量、预算上限、剩余额度、估算/实际差异和超限原因。 | 状态：✅ merged 到 main（PR #348）；代码证据：待补；测试证据：待补；PR/Commit：待补；CI：待补。 | #348 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-72-05` | #72 | 恢复/重试/子 Run 必须继承并正确扣减预算，防止通过重试或委派绕过预算。 | 状态：✅ merged 到 main（PR #357）；代码证据：待补；测试证据：待补；PR/Commit：待补；CI：待补。 | #357 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |
| `C-72-06` | #72 | 为边界值、未知 tokenizer、模型窗口差异、并发扣减、重试和子 Run 补单测及 PostgreSQL 集成测试。 | 状态：✅ merged 到 main（PR #358）；代码证据：待补；测试证据：待补；PR/Commit：待补；CI：待补。 | #358 | 未见项级 CI 终态记录 | 未提供该原子项的生产现场证据；按生产验收登记 NO-GO |

## 5. 证据回填规范与完成定义

每个原子 ID 后续必须补齐以下字段；不适用时填写原因，不能留空：

1. **main 实现证据**：文件:行号、类/函数/API method+path、migration/table/key、前端路由/组件；记录核验 SHA。
2. **测试证据**：测试文件与具体 test node、测试命令、依赖类型（mock/in-memory/真实 PostgreSQL/Redis/OCI/Provider）、实际结果。
3. **PR/commit**：实现 PR、merge commit 或未合并 PR HEAD；明确是否已进入当前 main。
4. **CI 证据**：workflow run URL、run ID、head SHA、job/check 名称、终态 conclusion、测试摘要和 artifact 链接。queued/in_progress/skipped/cancelled 均不能算 pass。
5. **生产验收**：目标环境/版本、责任人、UTC 时间、变更单、真实操作记录、日志/指标/数据校验、回滚结果、独立签核；无需生产验证的条目需说明为何 N/A。
6. **最终结论**：实现完成 / 自动化通过 / 现场待验收 / 生产通过 / 失败 / 阻塞。一个层级通过不能自动提升其他层级。

推荐状态转换：

`待核验 → 已定位实现 → 自动化待执行 → 自动化通过 → 现场待验收 → 生产验收通过`

任一 CI failure、test skipped、证据 SHA 不匹配、生产记录缺失或回滚未验证，都应保留在相应未完成状态。

## 6. 与差距审查成果的映射

| 审查产物 | 主要内容 | 在本矩阵中的用途 | 不可推导的结论 |
|---|---|---|---|
| A4 #863 | 191 项目标能力原子清单 | 能力需求目录与 #267 原子执行台账交叉索引 | 不能证明每项代码已实现 |
| A5 #871 | 191×7 类前后端/API/Service/Repo/迁移/权限/测试路径矩阵 | 代码证据入口 | 路径存在不等于接线、授权或测试通过 |
| B4/B5 #872/#874 | 三平面架构、资源/组织/资产生命周期 | 生产验收边界与依赖识别 | 架构图不等于部署验收 |
| C4 #866 | 仓库模块覆盖台账 | 说明审查覆盖范围和未读路径 | 不等于逐文件语义审查 |
| C5 #868 | CI/测试执行记录 | 追踪历史失败、跳过和待复验项 | PR 文档 CI 不等于全量测试通过 |
| D3/D4 #859/#873 | 差距归并与 54 项研发任务 | 唯一实施任务、依赖和优先级入口 | Backlog 不等于实施完成 |
| D5 #877 | 总体审查整合和关闭条件复核 | 维持未闭合项和 Issue #848 关闭门槛 | 报告合入不等于产品验收 |

## 7. 当前 Gate 与行动顺序

- **G0 / P0**：先修复真实 CI 暴露的身份/租户、权限、文件 API、迁移和发布证据失败；对每个原子项保留失败 job 与日志，不能通过删减测试范围消除失败。
- **G1 / P1**：补齐 Runtime/Workflow/Multi-Agent、Connector/Memory、事件恢复、评测发布、HA/DR 的端到端集成证据；真实环境验收按单独授权和现场计划执行。
- **G2 / P2**：补齐 DigitalEmployee、统一资产供应链、Marketplace/伙伴/License、成本/SLA/价值运营等产品闭环。
- CI 失败项修复应由对应领域 Issue 唯一承接；本矩阵只做证据索引，不复制领域实现，不创建重复任务。
- 对 #267 的 A-12-02，保留生产 OCI 隔离现场验收阻塞；不能因 PR #946 增加了 probe 或 CI 运行而勾选完成。

## 8. 自检、CI 与边界

- 从 Issue #267 正文提取并去重 #8–#31、#72 的 C/A 编号：**186 个唯一 ID**；额外战略任务编号不混入本矩阵。
- 每个 ID 均包含原子需求描述、条目状态摘要、PR 索引、CI 状态字段和生产验收字段。
- 当前 main SHA 已记录；本轮查询到 main check-runs 6 项，其中 5 queued、1 in_progress，故 main CI 状态为 pending。
- 生产验收登记明确 NO-GO；本轮没有申请或连接真实服务器、生产数据、外部凭据或真机。
- 本文是证据整合与追踪，不宣称 186 项逐条重跑测试或完成生产验收；实际项级状态必须继续按第 5 节规范更新。
- PR CI 仅验证本次文档与矩阵静态结构；不会自动运行 186 项所有业务场景。

