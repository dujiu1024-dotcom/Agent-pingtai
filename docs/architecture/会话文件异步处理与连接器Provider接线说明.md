# 会话文件异步处理与连接器 Provider

关联：Issue #8

## 文件上传与处理

- 生产上传服务组合 `PostgresSessionRepository`、`S3ObjectStorage` 和 `PostgresFileProcessingQueue`。
- 将 queue 注入 `SessionAssetService(processing_queue=...)`。上传先写对象和元数据，资产状态为 pending，再写入持久化处理队列；入队失败时资产拒绝并删除对象。
- Worker 由宿主调度器循环调用 `FileProcessingWorker.run_once()`。Worker 通过租户工厂取得会话仓储、对象存储、知识服务；扫描器必须配置，扫描失败/超时不会放行。
- Worker 校验对象 SHA-256，执行病毒/内容扫描，再通过 ParserRegistry 解析 PDF、DOCX、XLSX、PPTX、HTML 或纯文本，最后将文档写入知识库。项目文件继承 project scope；非项目文件进入 owner private scope。
- 队列使用 PostgreSQL `FOR UPDATE SKIP LOCKED` 领取、租约超时接管、指数退避和 dead-letter；处理成功幂等完成。达到最大尝试次数后文件标记 rejected 并删除对象。
- 新增 migration `027_file_processing_jobs.sql`。部署时需按顺序应用所有 PostgreSQL migrations。

## 连接器撤销与清理 Worker

- 注入 `PostgresConnectorCleanupQueue` 到 `DataConnectorService(cleanup_queue=...)`。撤销接口先持久化本地 revoked 状态，再将远端 OAuth revoke 和下游数据清理写入 migration 029 的持久化队列。
- `ConnectorCleanupWorker` 由宿主 scheduler 轮询；远端撤销和下游清理分步骤记账，部分成功后重试只执行未完成步骤。
- 队列使用 SKIP LOCKED、Worker 租约心跳、租约超时接管、指数退避和 dead-letter。撤销失败不会恢复本地访问权限。
- disposition=delete 时清理同步内容；retain 时保留已同步内容但撤销后不再继续同步。清理策略由授权调用者明确传入。
- 新增 migration `029_connector_cleanup_jobs.sql`。

## 连接器 Provider

- `HTTPJSONConnectorProvider` 是 REST/JSON 连接器的通用 Provider 适配器。Base URL 必须为 HTTPS 且 host 命中部署 allowlist；列表和撤销路径由可信宿主配置，不能由用户数据控制。
- 访问凭据由 DataConnectorService 的 SecretResolver 在执行时注入；请求禁用重定向、限制响应大小、校验 JSON items/cursor/has_more 结构。
- 不同 SaaS 的分页、字段映射、增量时间戳、删除 tombstone 和 OAuth 撤销差异，仍应由各自 Provider 配置/实现负责；本适配器不宣称替代真实 SaaS 联调。

## 生产部署需注入

- S3-compatible client、bucket、SSE/KMS 参数。
- PostgreSQL connection factory 和 migration 027。
- 文件扫描服务（ClamAV/云安全扫描等）及知识库 ParserRegistry/Embedder。
- Worker scheduler、每租户仓储/对象存储/知识服务工厂。
- HTTP connector AsyncClient、允许的 host、列表路径、撤销路径和 SecretResolver。

Worker scheduler 应按固定轮询周期调用 run_once；队列本身负责可用时间、租约过期接管和指数退避，不应由 scheduler 对失败任务做无条件立即重放。

上线前还需对队列租约过期接管执行至少一次双 Worker 并发演练，并验证拒绝文件不会进入知识检索索引。

真实对象存储、病毒扫描、第三方连接器 OAuth、外部 API、Worker 多进程崩溃恢复及部署权限仍需在目标环境由用户验证。
