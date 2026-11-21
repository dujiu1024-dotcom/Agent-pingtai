# B1 专项审查：十项核心技术能力（6–10）

- Issue：[#848](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/848)
- 审查范围：仅第 6–10 项核心技术能力（Webhook/开放 API、多模型 Harness、Agent 发布部署、执行追踪质量、企业部署基础设施）
- 基线：`main` @ `b622d5fc9eab1c79c28dcaa13367eec3c138679d`
- 审查日期：2026-11-21
- 方法：读取基线 Git tree、后端实现、API 组合根、数据访问/运行时模块、部署配置、专项测试和 CI workflow；本报告只判断仓库可见证据，不将文档声明等同于生产验收。
- 限制：本轮未申请真实服务器/外部资源，未执行真机、生产网络、真实模型供应商、生产发布、跨主机灾备或生产恢复演练。当前执行环境不能直接启动仓库服务或访问真实部署依赖；因此下文将代码/自动化测试存在与实际运行验收分开记录。

## 一、结论摘要

五项能力均已有可复用的实现基础，但均未达到“完整、可验证的企业级生产能力”：

| 编号 | 能力 | 成熟度 | 当前可确认的实现 | 主要差距 |
|---|---|---|---|---|
| 6 | Webhook 与开放 API 生态 | 基础已完成 / 部分实现 | 版本化 API 前缀、Python SDK、租户订阅管理、HMAC、DNS pinning、持久化投递、重试/死信/审计重放 | OpenAPI 契约/SDK 覆盖不完整；API 凭据、配额/限流/开发者门户与外部伙伴生命周期未形成完整产品闭环；真机投递未验收 |
| 7 | 多模型运行时与 Harness | 部分实现 | Run 快照模型解析、OpenAI-compatible 路由与 fallback、LangGraph/Strands 适配、MCP Gateway、能力声明和基准门禁 | Provider 类型和 Harness 适配覆盖有限；跨 Harness 一致性/生产基准证据不足；第三方依赖组合及真实模型端到端未验收 |
| 8 | Agent 版本发布与部署管理 | 部分实现 | 版本化部署状态机、canary/blue-green/rolling、健康门禁、回滚策略、升级 manifest、迁移兼容规划、release evidence gate | 部署状态机为内存对象；升级脚本只生成计划/验证证据，不实际发布、切流或回滚；缺统一 Agent/Workflow/Skill/Tool 资产版本到环境发布闭环 |
| 9 | 执行链路追踪与质量分析 | 基础已完成 / 部分实现 | W3C trace context、ContextVar span、PostgreSQL Run trace、组件 span、脱敏、评测数据集/回归比较/人工评审/发布门禁 | Trace exporter/后端可视化、告警/SLO/质量运营闭环不完整；部分遥测故障隔离会吞掉导出失败；生产采样、保留、容量和真实质量基准未验收 |
| 10 | 企业级部署与运行基础设施 | 部分实现 | PostgreSQL/Redis/MinIO Compose、生产 Compose 加固、健康检查、备份策略/工具、升级/恢复文档、CI 集成服务 | 主要是单节点 Compose；缺 Kubernetes/Helm/HA 与多地域控制面模板；数据库和对象存储身份权限过宽；网络出口强制策略依赖外部环境；生产恢复/离线混合部署未验收 |

**总体判断：** 当前不是“只有 Demo”，有相当数量可复用的后端机制、迁移、SDK、CI 和专项测试；但其中多个模块仍是 provider-neutral 状态机、计划生成器、接口端口或 mock 驱动测试。不能将它们标记为已完成的生产发布平台、多模型生态或混合部署基础设施。

## 二、逐项代码审查

### 6. Webhook 与开放 API 生态

**成熟度：基础已完成 / 部分实现。**

#### 已实现证据

- 版本化 API 与 SDK：
  - `agent_platform/sdk/client.py`：`PlatformClient` 支持 HTTPS origin、Bearer token、API version header、request ID、有限超时、GET/POST/PUT/PATCH/DELETE、分页、Retry-After；POST/PATCH 重试要求显式 idempotency key。
  - `docs/sdk/python.md`、`docs/architecture/开放APIWebhookSDK与扩展契约.md`：记录 v1 路径 `/api/platform/v1` 与兼容策略。
  - `tests/test_sdk_public_contract.py`、`tests/test_sdk_retry_method_matrix.py`：SDK 公共契约和重试矩阵测试。
- Webhook 管理和授权：
  - `agent_platform/api/webhooks.py`：从认证 Principal 取得 tenant/user；管理授权由服务端回调判断；支持订阅创建、列表、密钥轮换、禁用；管理操作写审计。
  - `agent_platform/events/admin.py`：Secret Manager 保存密钥并只在数据库保存引用；创建失败撤销新密钥；轮换/禁用通过持久化撤销队列完成旧密钥撤销。
  - `agent_platform/production.py`：生产组合根创建 `PostgresWebhookStore`、`WebhookSubscriptionService`、`WebhookDeadLetterReplayService` 和生产 Webhook Worker，并将订阅/死信管理服务、授权器、Worker 注入 `create_platform_app`。
- 投递安全和可靠性：
  - `agent_platform/events/webhooks.py`：HMAC-SHA256、时间戳窗口、租户隔离、租约与 lease token fencing、重试/死信、DNS 公网地址验证。
  - `agent_platform/events/webhook_http.py`：连接 DNS pin 后的公网 IP，保留 Host/TLS SNI，拒绝 redirect，不使用通用代理自动跳转。
  - `agent_platform/events/webhook_replay.py`、`agent_platform/events/webhook_operations.py`：死信重放有授权、操作原因、审计与租户约束。
  - `agent_platform/events/webhook_worker.py`：显式生产 transport、Secret resolver、审计 sink 和 Worker 生命周期。
  - `agent_platform/storage/postgres/023_webhooks.sql`、`076_webhook_secret_revocations.sql`、`077_webhook_secret_revocation_leases.sql`：Webhook 与密钥撤销持久化迁移。
- 专项测试：
  - `tests/test_webhook_admin.py`
  - `tests/test_webhook_http.py`
  - `tests/test_webhook_worker.py`
  - `tests/test_webhook_dead_letter_replay.py`
  - `tests/test_webhook_operations_replay.py`
  - `tests/test_webhook_secret_revocation_leases.py`
  - `tests/test_issue_20_a_20_02_webhook_security_reliability.py`
  - `tests/test_webhooks_sdk_extensions.py`

#### 具体缺失与风险

1. SDK 是通用 HTTP 客户端，不是完整平台 SDK：没有看到围绕 Agent、Workflow、Knowledge、Task、Session、Webhook 订阅/投递的完整类型化资源客户端、分页模型和版本兼容矩阵。
2. 仓库存在 API 路由与文档，但没有发现已发布、版本化、可供外部伙伴锁定的 OpenAPI JSON/YAML 工件，也没有 SDK 自动生成/发布流水线的充分证据。FastAPI 运行时可能提供 schema endpoint，不等于契约已纳入版本治理。
3. 代码证据覆盖 Webhook outbound event delivery 和管理 API；没有据此证明 inbound webhook 接入、全量事件 topic 目录、订阅过滤表达式、伙伴回调自助管理、开发者门户、API 产品套餐、API Key 生命周期/细粒度 scope、全局配额/限流、使用账单和审计报表均已完成。
4. Webhook 传输层对 DNS pinning 做了应用级约束，但生产网络层仍需 firewall/egress gateway/NetworkPolicy 强制阻止绕过代理的直连；环境变量配置代理不等于网络强制控制。
5. 生产组合根使用本地 Secret Store 作为 resolver；企业部署的 Vault/KMS、密钥轮换跨实例传播和灾备恢复仍需部署级验证。
6. 目前自动化测试存在大量 fake store/transport；数据库租约、DNS/证书、网络中断、远端已收到但本地确认前崩溃的重复投递窗口，需要真实 PG/Redis 和接收端集成测试进一步覆盖。

**建议：** P1 补 OpenAPI contract CI、API credential/scope/quota/rate-limit/usage 体系和 SDK 发布；P1 增强 webhook 端到端 PG/Redis/接收端故障测试；P0/P1 继续落实强制 egress 控制与最小权限 Secret Store。

### 7. 多模型运行时与 Harness 架构

**成熟度：部分实现。**

#### 已实现证据

- 模型注册快照与路由：
  - `agent_platform/models/registry_resolver.py`：只从 Run pinned registry snapshot 解析 Model Template / Provider 版本，租户绑定 secret ref，校验 provider URL 与数据等级。
  - `agent_platform/models/runtime_router.py`：OpenAI-compatible transport、显式 fallback template、circuit breaker、usage recorder、成本估算、trace span 和 audit sink；usage 通过 `(tenant_id, run_id, request_id)` 幂等写入。
  - `tests/test_model_runtime_router.py`：覆盖 fallback、用量记录、凭据和失败路径。
- Harness 统一适配：
  - `agent_platform/runtime/protocols.py`、`adapter_contract.py`：定义 Harness 请求/结果、媒体输入输出、能力声明、SemVer 合约和不支持能力的 fail-closed 行为。
  - `agent_platform/runtime/langgraph_adapter.py`：LangGraph 工具节点限定为 GatewayToolNode；工具身份来自可信运行上下文和 allowlist。
  - `agent_platform/runtime/strands_agents.py`、`strands_mcp_provider.py`：Strands Agents SDK 适配；MCP session 绑定 RunContext、短期 token 和允许工具集合。
  - `agent_platform/runtime/deepseek_harness.py`：存在 DeepSeek 专项适配实现。
  - `agent_platform/runtime/harness_factory.py`：将 Harness 强制包装为 quota-enforced adapter。
- 基准与门禁：
  - `agent_platform/runtime/harness_benchmark.py`、`harness_benchmark_suite.py`：固定任务集、成功率、质量锚点、延迟、TTFT、Token、成本、工具调用和能力跳过记录。
  - `agent_platform/runtime/harness_baseline_gate.py`：按最小样本、成功率、质量、p95 延迟、跳过比例和成本门槛 fail-closed。
  - `tests/test_harness_composition.py`、`tests/test_harness_benchmark_suite.py`、`tests/test_harness_baseline_gate.py`、`tests/test_deepseek_harness_adapter.py`、`tests/test_harness_media_contract.py`。
  - `.github/workflows/harness-benchmark-poc.yml`：对 Harness benchmark 相关文件变更运行专项单测、Ruff 和 Mypy。

#### 具体缺失与风险

1. Model Provider 当前 resolver 明确只接受 `openai-compatible` provider_type。不同厂商的原生工具调用、结构化输出、缓存、推理参数、文件/音视频能力和错误语义仍需独立 adapter 与能力声明；不能把 OpenAI-compatible 接口等同于供应商原生能力全覆盖。
2. 当前 Harness 有 LangGraph、Strands 和 DeepSeek 相关实现，但未看到 LangGraph/Strands/其他 Harness 的完整兼容性矩阵、版本锁定矩阵和生产支持承诺。Strands 依赖是可选依赖，未安装/未运行环境不能判定生产可用。
3. Benchmark 测试中包含 CandidateAdapter/Fake transport；固定数据集与 gate 有价值，但并不等同于对真实模型、真实工具链、并发负载、供应商故障切换和任务质量进行过基准验收。
4. `HarnessBaseline` 的质量分来自固定测试锚点；代码本身也明确说明不代表人工评测，且当前 latency 是顺序测量，不是并发压测。
5. 未找到统一的 provider capability registry、在线健康探测与动态路由控制面全链路证据；模型路由和 Harness 路由目前仍是相邻但不同的抽象层。
6. 跨运行时一致性测试、跨 provider 回归数据集、真实凭据轮换/熔断恢复、供应商 SLA 观测和成本价格卡治理仍需补齐。

**建议：** P1 建立 Provider/Harness capability matrix 和版本兼容测试；P1 将真实 provider contract test 与合成 benchmark 分开；P2 扩展原生 provider adapter 和生产运行指标采集。

### 8. Agent 版本发布与部署管理

**成熟度：部分实现。**

#### 已实现证据

- `agent_platform/deployment/versioned.py`：Deployment/DeploymentState/DeploymentStrategy；支持 rolling、blue-green、canary、gray；维护窗口、流量阶段、最小样本、错误率/p95 延迟门禁、自动回滚/人工暂停和历史事件记录。
- `agent_platform/deployment/migrations.py`：expand → dual-write → backfill → validate → cutover → contract 的迁移阶段机，校验 schema/app 兼容和 rollback compatibility。
- `scripts/release_upgrade.py`：校验 SemVer、镜像 digest、配置 revision、兼容窗口、备份引用、迁移策略、分批比例、健康门禁和回滚信息；生成升级/回滚计划。
- `scripts/release_gate.py`：验证 schema-v2 release evidence、精确 commit SHA、artifact/archive digest、检查项、时间戳新鲜度、独立审批人和 blocker。
- `docs/deployment/versioned-rollout.md`、`docs/deployment/release-upgrade.md`、`docs/release/PRODUCTION_RELEASE_ACCEPTANCE.md`、`docs/release/PRODUCTION_ACCEPTANCE_REGISTER.md`。
- `tests/test_versioned_deployment.py`、`tests/test_release_upgrade.py`、`tests/test_release_gate_evidence.py`、`tests/test_issue_21_a_21_02_release_gate_blocking.py`。

#### 具体缺失与风险

1. `Deployment` 是内存数据类/状态机，没有发现与生产数据库持久化、跨进程恢复、并发租约、操作者授权、审计、真实流量控制器之间的完整绑定。单元测试只证明状态机转换。
2. `scripts/release_upgrade.py` 和 `scripts/release_gate.py` 都明确是 validator/planner：不部署容器、不执行迁移、不切流、不执行回滚。不能将 manifest 通过等同于 release 已完成。
3. 未发现统一的 Agent/Workflow/Skill/Tool/Prompt/Connector 资产版本构建产物、依赖锁定、兼容性扫描、签名、制品库、环境晋级、灰度发布和回滚的端到端流水线。
4. Prompt rollout bridge `agent_platform/observability/evaluation_rollout.py` 是实验结果到 Prompt 灰度策略的桥接，不等同于 Agent/Workflow 全资产部署系统。
5. 生产 Release Gate 要求 deployment preflight、E2E、fault injection、performance、security、backup_restore、production_smoke、migration、rollback 全部提供新鲜证据；仓库的 acceptance register 明确整体 NO-GO，真实生产验收仍未完成。

**建议：** P0/P1 把部署状态机落到持久化 Deployment Repository，并引入受控 Deployment Executor adapter；P1 将资产版本、环境配置、制品签名/依赖锁和审批接入 Release Gate；生产 smoke、迁移、回滚只在授权发布流程中验证。

### 9. 执行链路追踪与质量分析

**成熟度：基础已完成 / 部分实现。**

#### 已实现证据

- `agent_platform/observability/trace_context.py`、`trace_middleware.py`：W3C trace context 与 ASGI request 关联。
- `agent_platform/observability/trace_spans.py`：ContextVar 父子 span，组件维度覆盖 request/run/agent/model/prompt/tool/retrieval/knowledge/blackboard/connector/custom，记录时间、耗时、状态和关联 ID；属性经过 redaction。
- `agent_platform/observability/run_trace.py`：RunTraceEvent、PostgresRunTraceRepository、事件序列、租户隔离、事件 ID 去重、LangGraph callback bridge；生产组合根创建 PG repository 并在启动时确保 schema。
- `agent_platform/observability/instrumentation.py`：Langfuse/metrics 等 observer failure isolation。
- `agent_platform/api/observability.py`：metrics、health/capacity、alert 和 evaluation endpoints；评测 API 依赖明确 service/authorizer 注入。
- `agent_platform/observability/evaluation_runner.py`、`evaluation_governance.py`：固定 dataset version 评测、阈值门禁、baseline/candidate 指标对比、policy version、内容 hash 和证据引用。
- `docs/observability/cross-component-tracing.md`、`docs/observability/evaluation-governance.md`。
- 测试：`tests/test_trace_context.py`、`test_trace_middleware.py`、`test_trace_spans.py`、`test_run_trace.py`、`test_trace_span_correlation.py`、`test_evaluation_governance.py`、`test_evaluation_runner.py`、`test_observability.py`。

#### 具体缺失与风险

1. TraceSpan 的 exporter 是回调式接口；当前实现证明了 span 创建和导出端口，不足以证明已部署 OpenTelemetry Collector/Tempo/Jaeger 等统一后端、长期存储、跨服务采样和查询体验。
2. `safe_observation`/`safe_metric` 对 exporter/observer 异常采取 fail-open/吞错，保护业务执行，但没有看到遥测丢失率、export queue backlog、drop count 和 exporter health 告警的完整证据。
3. Trace 和 RunTrace 两套模型各有价值，但跨组件关联、采样、事件顺序、保留周期、分区归档、冷热数据生命周期和高基数维度控制仍需统一治理。
4. Evaluation runner 与 governance 有代码和测试；但评测数据集规模、人工标注质量、线上反馈采样、跨模型版本对比、质量退化自动阻断发布、评测成本和覆盖率报表尚未形成可证明的持续运营闭环。
5. 现有质量指标有固定锚点/阈值，但不能代表业务价值、真实用户满意度或复杂任务成功率；生产级 SLO/SLA、质量目标、告警升级与值班闭环未验证。

**建议：** P1 明确 Trace backend/exporter/retention/采样规范和遥测自监控；P1 将 Evaluation gate 与 Agent release pipeline 绑定；P2 建设质量、成本、SLA 的统一运营看板和反馈闭环。

### 10. 企业级部署与运行基础设施

**成熟度：部分实现。**

#### 已实现证据

- `deploy/compose.yaml`：PostgreSQL(pgvector)、Redis、MinIO、持久卷、健康检查、loopback 端口、资源限制和凭据必填。
- `deploy/production.compose.yaml`：生产镜像变量、非 root、read-only filesystem、cap_drop、no-new-privileges、tmpfs、healthcheck、资源限制、文件 secrets、loopback 暴露、Postgres/Redis/MinIO 健康依赖、出站代理环境变量。
- `agent_platform/production.py`、`production_config.py`：生产 ASGI 组合根和配置校验；包含 PostgreSQL/Redis/对象存储、Run worker、Webhook worker、Outbox、审计、quota 等服务装配。
- `agent_platform/deployment/backups.py`、`deploy/BACKUP_POLICY.md`、`deploy/backup-policy.json`、`deploy/disaster-recovery-policy.json`、`docs/deployment/backup-disaster-recovery.md`：备份策略、catalog/scheduler/restore drill 抽象和恢复文档。
- `scripts/deploy_infrastructure.sh`、`scripts/release_upgrade.py`、`scripts/release_gate.py`、`docs/deployment/production-environment.md`、`docs/deployment/health-readiness-probes.md`。
- `tests/test_deployment_compose.py`、`tests/test_production_deployment_contract.py`、`tests/test_backup_recovery_scripts.py`、`tests/test_postgres_backup_restore_integration.py`。

#### 具体缺失与风险

1. 仓库可见的生产部署主路径是 Docker Compose 单节点形态；递归树未发现 Kubernetes/Helm/Operator/多节点编排模板。不能据此宣称已具备企业私有云/本地/自有云 HA 标准化交付。
2. Compose 有 PostgreSQL/Redis/MinIO 持久化和备份设计，但跨组件一致性快照、异地复制、自动恢复编排、RPO/RTO 达标演练和全平台灾备演练仍缺真实证据。
3. `production.compose.yaml` 将 API/Worker 的 PostgreSQL 用户配置绑定到同一 `POSTGRES_USER`，对象存储使用 MinIO root credential；这与数据库 runtime least-privilege 和 bucket-scoped service identity 目标不一致，属于生产配置高风险项。
4. Compose 注入 HTTP(S)_PROXY 和 EGRESS_ALLOWED_HOSTS 环境变量，但仓库本身未提供网络层拒绝直连的 firewall/NetworkPolicy/egress gateway 策略工件；代理环境变量本身不能强制所有出站流量经过代理。
5. API readiness checks 在 `production.py` 当前包含 postgres、redis、quota_schema；Compose 依赖 MinIO 初始 healthy，但 readiness 未见对象存储运行期探测。对象存储运行中断时可能出现 readiness 与真实依赖状态不一致。
6. 企业运行平面与公共控制平面解耦、公共控制面不可用时企业侧离线继续运行的验收证据不足；当前代码和部署配置不能证明控制平面/生态服务断网场景下的授权、许可证、资产分发和已部署业务继续运行。
7. 生产 Compose 中数据库、Redis、MinIO、API、Worker 及服务身份的高可用、自动升级、容量弹性、跨节点调度和版本回滚还未形成标准集群方案。

**建议：** P0 拆分 DB migration owner/API runtime/worker/audit roles 与 MinIO bucket-scoped identity；P1 提供强制 egress policy、Kubernetes/Helm 或企业部署参考拓扑、对象存储 readiness；P1 完成跨组件一致性备份与恢复演练；P2 建立 Hybrid control-plane/enterprise runtime 离线自治验收套件。

## 三、测试与 CI 证据矩阵

以下是仓库中已找到的专项自动化测试文件，不代表本轮已运行通过：

| 能力 | 主要测试文件 | 证据性质 / 限制 |
|---|---|---|
| Webhook/API | `test_webhook_admin.py`、`test_webhook_http.py`、`test_webhook_worker.py`、`test_webhook_dead_letter_replay.py`、`test_issue_20_a_20_02_webhook_security_reliability.py`、`test_webhooks_sdk_extensions.py`、SDK contract/retry tests | 包含 fake store/transport 与单元测试；部分 PG/Redis 集成在独立 workflow |
| Harness | `test_model_runtime_router.py`、`test_harness_composition.py`、`test_harness_benchmark_suite.py`、`test_harness_baseline_gate.py`、`test_deepseek_harness_adapter.py`、`test_harness_media_contract.py` | benchmark suite 含 fake adapters；真实 provider 运行未证明 |
| 发布部署 | `test_versioned_deployment.py`、`test_release_upgrade.py`、`test_release_gate_evidence.py`、`test_issue_21_a_21_02_release_gate_blocking.py` | 状态机/manifest/evidence validator 测试，不会触发真实部署 |
| Trace/Evaluation | `test_trace_context.py`、`test_trace_middleware.py`、`test_trace_spans.py`、`test_run_trace.py`、`test_evaluation_governance.py`、`test_evaluation_runner.py` | 验证数据结构、回调、租户范围和评测门禁；生产 exporter/容量未验证 |
| Infra/Backup | `test_deployment_compose.py`、`test_production_deployment_contract.py`、`test_backup_recovery_scripts.py`、`test_postgres_backup_restore_integration.py` | Compose 静态契约与自动化恢复测试，不是异地生产灾备演练 |

CI 文件证据：
- `.github/workflows/platform-release-gate.yml`：PR/push 主流程；含 PostgreSQL/Redis service、Webhook security reliability、Run/Tool Gateway、备份恢复、评测和静态检查等测试。
- `.github/workflows/platform-integration.yml`：PostgreSQL/Redis/Vault 服务集成测试。
- `.github/workflows/harness-benchmark-poc.yml`：Harness benchmark 文件变更时执行专项测试、Ruff、Mypy。
- CI workflow 定义和测试文件存在，不等于本轮 B1 PR 的 CI 已通过。本轮以该 PR 的 GitHub Actions 实际结果为最终 CI 状态；若尚未完成则标记 pending。

## 四、差距 Backlog（仅 B1 范围）

| 优先级 | 建议工作项 | 依赖 | 可验证验收标准 |
|---|---|---|---|
| P0 | 拆分 PostgreSQL migration/runtime/audit 身份与 MinIO bucket-scoped identity | 生产配置、Secret Manager、迁移/Release Gate | CI/部署检查证明 runtime role 非 superuser、非 owner、无 DDL/TRIGGER 权限；MinIO 凭据仅访问指定 bucket |
| P1 | API 契约与开放平台补齐 | Identity/credential、SDK | OpenAPI 工件版本化；API Key scopes、限流/配额、审计、幂等矩阵有集成测试；SDK 覆盖核心资源并发布兼容矩阵 |
| P1 | Webhook 可靠投递与网络边界 | Postgres/Redis、Secret Manager、egress policy | 多实例 claim/fencing、远端成功后 worker 崩溃、DNS rebinding、重放审计、密钥轮换传播均有可重复集成测试 |
| P1 | Provider/Harness 能力矩阵 | Runtime Adapter Contract、模型注册 | 每个 adapter 具备版本/能力声明；跨 Harness 一致性和真实 provider contract test；基准报告包含质量/成本/延迟/恢复 |
| P1 | 持久化 Deployment Executor | Asset Registry、Deployment Repository、审批审计 | 部署状态跨进程恢复；canary 失败可自动回滚；部署记录有 tenant/actor/approval/audit；不以 planner 结果冒充部署成功 |
| P1 | Trace exporter/telemetry SLO | TraceSpan、RunTrace、Metrics | 生产 exporter、采样、保留、队列积压、丢失率和 exporter health 告警均有测试和运行指标 |
| P1 | 企业级部署模板与依赖健康 | Compose 现状、production config | Kubernetes/Helm 或等价 HA 拓扑；Postgres/Redis/Object Store readiness；强制 egress policy；离线企业侧自治测试 |
| P1 | 一致性备份与灾备演练 | Backup Catalog、Postgres/Redis/MinIO | 同一恢复点覆盖 DB/消息/对象/配置/密钥；异地复制；RPO/RTO 实测；恢复后业务一致性校验 |
| P2 | 资产全生命周期发布市场 | Asset Registry、SDK/OpenAPI、Deployment Executor | Agent/Workflow/Skill/Tool/Connector 版本、依赖、签名、环境晋级、撤销、灰度和回滚闭环 |
| P2 | 质量与成本运营中心 | Trace/Evaluation/Quota | 生产模型质量、SLA、成本和部门归集形成可查询报表；反馈可回归并受审批控制 |

## 五、本轮验证记录

- 基线：`main` SHA `b622d5fc9eab1c79c28dcaa13367eec3c138679d`。
- 代码证据：已读取上述核心实现、组合根、文档、测试和 workflow；仅对 B1 第 6–10 项做专项审查。
- 本地测试：未执行。当前会话执行环境未建立仓库依赖/PG/Redis/Vault/MinIO 运行栈；不能把测试文件存在写成测试通过。
- 真机/真实外部资源：按调度要求跳过。
- CI：由本报告 PR 自动触发仓库工作流；以 PR Checks 实际状态回填。
- 代码范围：本轮仅新增本专项审查报告，不修改产品运行代码，不关闭 Issue，不合并任何 PR。

## 六、最终判断

B1 第 6–10 项不是从零开始：Webhook 投递、SDK、Harness adapter、模型路由、部署规划、Trace、Evaluation、Compose 与备份均有可复用基础。最重要的判断边界是：

- **存在机制**不代表形成产品化平台；
- **有状态机/manifest**不代表真实部署器已执行；
- **有 Trace/评测代码**不代表生产观测和质量闭环已验收；
- **有 Compose/备份脚本**不代表企业 HA、离线自治和灾备已成立；
- **有 OpenAI-compatible provider**不代表完成多厂商原生能力适配。

建议按本报告 Backlog 继续拆分实施；不建议推倒现有实现重写。
