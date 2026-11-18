# 对外 HTTP API 契约（v1）

## 版本与兼容

- 稳定路径前缀：`/api/platform/v1`。路径版本是权威版本；`X-API-Version: v1` 是可选的一致性声明，省略该头的现有客户端保持兼容。
- 不支持的路径版本返回 404 `UNSUPPORTED_API_VERSION`；未知或不匹配的版本头返回 400。
- v1 的向后兼容变更限于新增可选字段、响应头和新资源/端点。不得删除字段、改变既有字段含义或收紧已承诺的输入约束。
- 破坏性变更必须使用新的主版本路径，并提供迁移窗口、弃用公告和迁移说明；不得在 v1 静默改变语义。
- v1 响应统一带有 `X-API-Version` 和 `X-Request-ID`。客户端可传入 1–128 位安全字符作为请求 ID；缺失或格式不合法时由服务端生成。

## 认证与授权

- `GET /api/platform/v1/bootstrap` 为公开启动元信息接口，仅返回 `auth.enabled`、应用版本和非敏感 capability 布尔值；不得返回密钥、凭据或租户配置。
- `auth.enabled=false` 时业务请求不要求 Principal；实例级只读 Office 状态通过部署显式配置的 `instance_tenant_id` 选择数据命名空间，不创建匿名用户或伪造 actor。
- 无身份请求的审计主体语义为 `actor=null`：不得将请求映射为默认用户；当前用户身份审计中间件跳过该类请求。若需持久化实例级审计事件，必须使用支持 nullable actor 的独立 system/instance audit sink。

- `/api/platform/v1/**` 受身份中间件保护，使用平台已配置的 Bearer/API credential 或安全会话认证方式。
- 认证失败为 401 `UNAUTHENTICATED`；身份有效但无资源/操作权限为 403 `FORBIDDEN`。
- 授权由具体业务路由按 tenant、principal、resource 和 action 执行。不得仅凭请求体或 URL 中的 tenant/resource 标识授权。
- 提供租户 ID、资源 ID 或幂等键均不能代替认证授权。

## 限流

- 对 `/api/platform/v1/**` 使用共享 Redis 滑动窗口，默认每个客户端 IP 与 HTTP method 每 60 秒 120 次；登录接口保留身份中间件自己的独立限流。
- 超限返回 429 `RATE_LIMITED`，提供 `RateLimit-Limit`、`RateLimit-Remaining`、`RateLimit-Reset` 和 `Retry-After`。
- Redis 限流后端不可用时 fail-closed，返回 503 `SERVICE_UNAVAILABLE`，不绕过限流继续处理。
- 当前基础限流维度为客户端 IP + method；生产反向代理必须正确配置可信客户端地址解析，不能直接信任任意客户端提交的转发头。

## 幂等

- 支持安全重试的写接口应声明支持幂等，并要求客户端发送 `Idempotency-Key`。键长度 1–128，仅允许 ASCII 字母、数字、点、下划线、冒号和连字符。
- 唯一作用域至少包含 tenant、principal、HTTP method、规范化 route 和 key；禁止跨租户、跨主体或跨路由重放结果。
- 同一作用域、同一请求摘要重试应返回首次执行结果；同 key 不同请求摘要返回 409 `CONFLICT`；处理中重复请求不得并发执行副作用。
- 幂等记录必须由路由使用的持久化存储实现，并定义保留期、并发占位/租约和失败恢复语义。当前契约模块提供键校验与作用域构造，不会自动为未接入持久化幂等存储的路由承诺重放能力。

## 分页

- 新增列表接口统一采用 `limit` + 不透明 `cursor`；默认 limit=50，最大 200。
- 建议响应字段为 `items`、`next_cursor`、`has_more`；末页 `next_cursor=null`。
- cursor 必须绑定租户和规范化查询条件并防篡改；使用 `encode_secure_cursor` / `decode_secure_cursor` 的 HMAC-SHA256 签名封装，签名密钥至少 32 bytes，默认有效期 1 小时（最长 7 天）。校验时必须同时验证签名、过期时间、tenant 与 query fingerprint；客户端不得解析、修改或构造 cursor。不得把未签名的 Base64 编码当作安全 cursor。
- 不把 offset/page-number 作为新列表 API 的默认协议。既有路由迁移到该协议时应保持现有客户端兼容，并逐路由补充契约测试。

## 错误模型

错误统一采用如下结构：

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed",
    "request_id": "req-123"
  }
}
```

| HTTP 状态 | 稳定错误码 |
|---:|---|
| 400 | INVALID_REQUEST、VALIDATION_ERROR、UNSUPPORTED_API_VERSION、API_VERSION_MISMATCH |
| 401 | UNAUTHENTICATED |
| 403 | FORBIDDEN |
| 404 | NOT_FOUND、UNSUPPORTED_API_VERSION |
| 409 | CONFLICT |
| 410 | GONE |
| 412 | PRECONDITION_FAILED |
| 422 | VALIDATION_ERROR |
| 429 | RATE_LIMITED |
| 500 | INTERNAL_ERROR |
| 502 | UPSTREAM_ERROR |
| 503 | SERVICE_UNAVAILABLE |
| 504 | UPSTREAM_TIMEOUT |

服务端 5xx 不得暴露异常文本、凭据、SQL、内部路径或 Provider 响应正文。客户端通过 request_id 关联服务端日志。

## OpenAPI 与变更流程

- FastAPI 自动生成的 OpenAPI JSON 是路由级机器可读 schema；Swagger UI 位于应用默认文档入口。
- 本文定义跨路由语义；新增/变更路由必须说明认证、授权动作、分页参数、幂等支持情况、错误码和版本兼容性。
- 契约 helper 存在不代表所有既有路由都已切换到 cursor 分页或持久化幂等。路由接线须有对应测试，不得仅凭文档宣称覆盖。


## 标准 HTTP 错误码扩展

除上表列出的核心状态外，常见 HTTP 失败状态也使用稳定、可分支处理的错误码：

| HTTP 状态 | 稳定错误码 |
|---:|---|
| 405 | METHOD_NOT_ALLOWED |
| 408 | REQUEST_TIMEOUT |
| 413 | PAYLOAD_TOO_LARGE |
| 415 | UNSUPPORTED_MEDIA_TYPE |
| 416 | RANGE_NOT_SATISFIABLE |
| 418 | REQUEST_REJECTED |
| 423 | RESOURCE_LOCKED |
| 424 | FAILED_DEPENDENCY |
| 428 | PRECONDITION_REQUIRED |
| 431 | REQUEST_HEADER_FIELDS_TOO_LARGE |

未显式登记的 4xx 映射为 `REQUEST_FAILED`；未显式登记的 5xx 映射为 `INTERNAL_ERROR`，并始终隐藏内部异常文本。客户端应优先依赖稳定错误码，不应解析 message 文本。
