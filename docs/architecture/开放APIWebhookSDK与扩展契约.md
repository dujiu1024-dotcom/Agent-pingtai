# 开放 API、Webhook、SDK 与扩展契约

## API 版本

平台对外 API 统一使用 `/api/platform/v1` 前缀。v1 内采用向后兼容的增量字段策略；破坏性变更需新建 v2，不在原版本静默改变字段语义。认证沿用平台 Identity Middleware，授权仍由服务端 Principal、资源授权器和 Tool Gateway 执行。

## Python SDK

`agent_platform.sdk.PlatformClient`：
- 强制 HTTPS Base URL。
- 自动添加版本化路径、Bearer Token 和 JSON 内容类型。
- 支持 Idempotency-Key。
- 对非 2xx 返回统一 `PlatformApiError`。
- Transport 可替换，便于单元测试和受控网络代理。

## Webhook

- 订阅表只保存 Secret Manager 引用，不保存签名明文。
- 投递表以 tenant + subscription + event 唯一约束去重。
- Outbox 事件按租户与 topic fan-out。
- Worker 使用 PostgreSQL lease + SKIP LOCKED 领取任务；每次 claim 返回独立 lease token，成功、重试和死信状态更新必须匹配当前 token，过期 Worker 不得覆盖已重新领取的投递。
- 失败记录只保存 HTTP 状态码和受控错误类型（不保存异常正文、URL 凭据或签名密钥）；重试采用有界指数退避，超过最大尝试次数进入 dead-letter。
- 订阅管理 API 从认证 Principal 派生租户/操作者，支持创建、列表、密钥轮换和停用；Secret Manager 只保存引用，变更前写入审计。
- 死信列表和授权重放均按租户隔离；重放要求管理授权与人工原因，重放请求、拒绝、失败和结果写入审计。
- 请求头包含 Event ID、Idempotency-Key、时间戳和 HMAC-SHA256 签名。
- 接收方使用 `timestamp + "." + raw_body` 计算 HMAC 并常量时间比较。
- 非 2xx/异常采用指数退避，超过最大尝试次数进入 dead-letter。
- 目标 URL 必须 HTTPS，拒绝凭据、localhost、私有/保留 IP 和常见内部域名。Dispatcher 现在要求 EgressPolicy 对每次投递解析并拒绝任何非公网 DNS 答案，并把完整 IP 集传给 post_pinned transport；生产 transport 必须只连接该 IP 集，同时保留原始主机名用于 TLS SNI/证书校验。生产网络仍必须通过出站代理实施第二道 DNS/IP 校验与重绑定防护；应用层 URL 检查不能替代网络 egress policy。

## 扩展清单

扩展声明唯一 ID、SemVer 版本、平台 API 版本、权限列表、依赖及每个依赖的精确 SemVer 版本锁和相对入口。安装计划必须核对依赖实际版本，禁止解析为未锁定或不匹配版本。未知权限应由平台注册中心拒绝；扩展不得直接获得数据库/密钥访问，不得绕过身份、审计和 Tool Gateway。当前提交提供 manifest 校验契约，不执行第三方代码加载。

## 国际化

租户 LocaleContext 独立保存语言和 IANA 时区；日期时间必须带时区并按租户时区格式化，数字格式使用显式分隔符，不修改进程级 locale。

## 未完成验收

Webhook 管理 API/管理 UI、订阅密钥轮换与撤销、真实 Worker 运行、死信重放 UI、完整 API OpenAPI/SDK 发布包、依赖锁定与卸载清理、DNS egress 代理、国际化文案覆盖仍需后续完成。生产网络/真机验证跳过，标注「待用户真机验证」。
