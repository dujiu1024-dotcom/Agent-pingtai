# C-12-03 Tool Gateway 执行契约

## 唯一工具派发边界

平台适配器必须通过 `ToolGatewayContract.call` 派发工具。该契约只接受传输层提供的 Bearer 凭据；`resolve_principal` 必须验证签名/持久化状态、过期与撤销，并返回服务端可信的 tenant、actor、roles、agent、Run 和 Run 工具 allowlist。不得从模型参数、MCP arguments 或请求体读取这些身份字段。

每次调用先验证凭据，再执行 Run 绑定授权（租户、actor、agent、Run 状态及归属），最后检查 Run 级工具 allowlist。任何 resolver/authorizer 异常均拒绝调用。资源级授权、风险等级、审批消费、幂等和 write-ahead audit 仍由 `SupervisedToolGateway` 强制执行；调用方不得直连 handler 或自行构造授权结论。

## Run 绑定

每个 Principal 必须绑定单一 tenant_id、actor_id、agent_id、run_id 和明确的 allowed_tool_names。身份由认证服务签发，不接受模型覆盖。RunAuthorizer 应在调用时查询可信 Run 状态，拒绝跨租户、非 owner、终态/已取消 Run 或已撤销权限。一个 Run 的凭据不得复用到其他 Run。

## 超时与取消

`timeout_seconds` 是单次工具调用的硬截止时间，必须为正数；默认 30 秒。超时会取消正在运行的 handler task，记录 `timed_out` 审计并抛出 ToolCallTimedOut。Run 取消通过 cancellation Event 传入，取消当前 handler 并记录 `cancelled`。上游 task 被取消时取消传播，不吞掉 CancelledError。工具 handler 必须协作式响应 asyncio cancellation；对无法确认副作用结果的外部操作，继续使用 Gateway 的持久幂等/对账机制，禁止盲目重试。

## 审计

Gateway 自身在授权/审批后、执行前写入 write-ahead audit；审计不可用时不得执行。执行结果/失败由 Gateway 记录。执行契约另记录认证/Run 拒绝、超时和取消事件（超时/取消使用审计动作 `tool.call.timed_out` / `tool.call.cancelled`，结果枚举使用兼容的 `failed`），包含 tenant、actor、Run、tool、call_id 和原因/截止时间，不记录 Bearer token、原始工具参数或工具结果正文。审计 sink 必须持久化且不可变；审计写入失败应 fail closed。

## 验证边界

单元测试覆盖无效凭据、Run 绑定拒绝、allowlist、超时、Run 取消、调用方取消和 Gateway 委派。真机/真实服务器/外部 Provider 验证按本次调度跳过；这些测试不宣称证明生产身份提供方或外部副作用系统的行为。


## 持久化 MCP Token 会话绑定

使用持久化 `RunTokenService` 的 MCP HTTP 宿主必须将 Token 记录中的 tenant、Run、Agent、actor 和 endpoint 与该 MCP bridge 的可信会话上下文逐项比较。任一字段不匹配、scope 为空或 endpoint 不一致时，在创建 MCP AccessToken 前拒绝请求。TokenService 仍负责验证令牌摘要、有效期和撤销状态；会话绑定检查防止一个合法 Token 被复用到另一个 actor 或 Run 的固定 bridge。
