# Webhook 交付契约

## 事件封装

每次投递使用稳定事件 ID，JSON body 为：

```json
{
  "id": "event-id",
  "type": "run.completed",
  "tenant_id": "tenant-id",
  "data": {}
}
```

body 使用 UTF-8、键排序和紧凑 JSON 编码。重试同一 delivery 时事件 ID 保持不变；接收方应以事件 ID 去重，而不能依赖请求到达次数。

## 签名

请求包含：

- `X-Platform-Event-Id`：稳定事件 ID。
- `X-Platform-Timestamp`：Unix 秒时间戳。
- `X-Platform-Signature`：`sha256=<lowercase hex HMAC-SHA256>`。
- `Idempotency-Key`：与事件 ID 相同。

签名原文为 `timestamp + "." + raw_body`，密钥至少 32 bytes。接收方必须对原始请求 body 验签，使用常量时间比较，并拒绝超过 300 秒的时间戳偏差（可配置但不得超过 900 秒）。验签前不要先解析再重新序列化 JSON。

## 订阅与密钥生命周期

订阅、列表、密钥轮换和禁用均从已认证 Principal 派生 tenant/actor；请求体中的 tenant 或 actor 字段不参与授权。列表不返回 secret reference。签名密钥由 Secret Manager 保存，数据库仅保存引用。创建或轮换在持久化失败时撤销新密钥；禁用后撤销关联密钥。管理操作在执行副作用前写入审计，审计不可用时 fail-closed。

目标地址必须为 HTTPS 公网地址，不得携带 URL 用户名/密码；每次投递重新解析 DNS 并将解析出的公网地址固定传给传输层，避免 DNS rebinding 将连接重定向到内网。生产传输层必须保持原 hostname 的 TLS SNI/证书校验，同时只连接固定地址。

## 投递、去重与重试

投递记录由共享 PostgreSQL 持久化，Worker 通过租约领取并使用 lease token 做 fencing。成功响应（2xx）完成 delivery；非 2xx、DNS/网络错误和超时进入指数退避重试，默认最多 8 次，退避从 2 秒起且上限 3600 秒。超过上限转入 dead-letter。过期租约可由其他 Worker 接管，旧 Worker 不得覆盖新租约结果。

远端可能已收到请求但本地在确认前崩溃，因此网络层语义是至少一次，不宣称 exactly-once。接收方必须按 `X-Platform-Event-Id` / `Idempotency-Key` 幂等处理。

## 死信与授权重放

死信列表和重放必须要求已认证主体及 webhook 管理权限，并按 Principal tenant 隔离。重放仅允许当前仍启用且仍订阅该 topic 的订阅；调用必须提供操作原因，经专用 replay service 执行并记录 actor、tenant、delivery、原因及结果审计。不得通过直接 Store 更新绕过授权/审计。

## 可观测与数据最小化

后台 Worker 使用显式 system actor；审计记录 event/subscription/delivery ID、topic、attempt、状态和响应码/错误类型，不记录签名密钥、完整 URL 查询串、原始事件正文或 Provider 响应正文。审计写入失败时不得开始外部投递。
