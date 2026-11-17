# Harness 结构化输出契约

## 执行边界

Harness Adapter 仍负责单次受监督执行；平台通过 `SchemaValidatedHarnessAdapter` 装饰器在统一边界校验 `HarnessRequest.output_schema`。业务 Agent 不应重复实现 JSON 解析或自行将未校验文本转为业务对象。

## Schema 注册与版本

- `StructuredOutputSchemaRegistry` 是单进程测试/开发实现；同一 `schema_id@version` 不可被不同内容覆盖。
- 多 worker 部署使用 `PostgresStructuredOutputSchemaRegistry`，以 `tenant_id + schema_id + schema_version` 精确寻址；注册同内容版本是幂等操作，注册不同内容到既有版本会失败。
- 每个版本保存规范化 JSON 与 SHA-256 摘要。数据库迁移使用 append-only 触发器禁止更新/删除，版本升级必须新增正整数版本号。
- Run 请求必须同时传入 `output_schema_id` 与 `output_schema_version`；不会隐式选择 latest。Schema 按 Run 的 tenant 解析，禁止跨租户命中。
- inline `output_schema` 与版本化引用不能同时传入，避免请求端覆盖已发布版本。

## 当前支持的 JSON Schema 子集

- 类型：object、array、string、number、integer、boolean、null
- 对象：required、properties、additionalProperties（仅 boolean）
- 数组：items、minItems、maxItems
- 字符串：minLength、maxLength
- 数值：minimum、maximum
- 枚举：enum
- 不支持的关键字、非 JSON 值、NaN/Infinity 和无法校验的 Schema 在注册/校验阶段 fail-closed。

## 修复重试与失败语义

默认最多进行一次修复，可配置 0–3 次；bool、负数和超出上限均拒绝。每次修复仍调用原 Harness Adapter，沿用同一 Run 上下文、模型模板和允许工具集合。修复次数是额外调用次数，因此总尝试数为 `max_retries + 1`。

达到上限仍不合格时抛出 `StructuredOutputValidationError`，错误对象包含最后一轮校验错误、总尝试次数和版本引用。异常必须向上抛出，由 Run 生命周期层将执行标记为失败或转人工；无效 JSON 不得作为成功结果。有效结果在元数据中附带解析后的结构化对象、尝试次数、校验状态及 schema 版本。

## 数据库部署

部署前应用 `agent_platform/storage/postgres/062_structured_output_schemas.sql`。运行账户需要对该表拥有 SELECT/INSERT 权限；不需要 UPDATE/DELETE 权限。PostgreSQL 注册表由宿主通过共享连接工厂注入，不在本模块中隐式创建数据库连接。

## 验收限制

单元测试覆盖 schema 子集、版本不可变、tenant 隔离、精确版本解析、重试上限和失败语义。真实 PostgreSQL/多 worker 部署仍需由集成环境执行迁移并验证数据库权限和触发器行为。
