# Runtime Adapter 契约（v1）

模型 Provider、Harness 与 Agent 执行器统一实现 `RuntimeAdapter`，并在 `manifest` 中声明 adapter 版本、可兼容的 Runtime Contract 版本区间及能力集合。

## 兼容策略

- 版本采用 `MAJOR.MINOR.PATCH`。Host 在实例化/注册 Adapter 时校验当前契约版本是否落在其声明的闭区间内。
- 兼容区间必须处于同一主版本；不允许跨主版本自动兼容。主版本升级必须显式适配。
- 不支持或未声明的能力按不支持处理；请求能力必须是 Adapter 声明能力的子集。

## 能力与执行边界

能力集合包含模态（text/image/pdf/audio/video）、流式、取消、恢复、结构化输出和工具调用。调用上下文必须绑定 tenant、Run、actor、request，并可选携带时区明确的 deadline 与 correlation id。Adapter 返回统一终态、用量及 Provider/模型/Adapter 版本元数据。

## 生命周期

Host 负责 Run 的持久化状态、租约、超时、恢复和审计；Adapter 负责一次请求的执行及对该 tenant/Run 的取消请求。取消必须幂等且不得跨租户或 Run。Adapter 结果只能返回终态（succeeded/failed/cancelled/timed_out），中间态由 Host 的 Run lifecycle 管理，不得把运行中的结果伪装为完成。

此契约定义端口与验证规则，不替代现有 `RunLifecycleManager`、Tool Gateway、模型路由或生产 Provider 接线。
