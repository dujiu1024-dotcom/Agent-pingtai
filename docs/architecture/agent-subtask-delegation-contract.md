# Agent 子任务委派契约（C-12-05）

## 委派边界

`SupervisedDelegationService` 只负责构造受监督的 Child Run 请求并调用宿主提供的持久化执行器；不直接调用模型或工具。宿主执行器必须通过平台 Task Center / Run Worker 创建和运行 Child Run。

- Child Run 必须继承 parent 的 tenant、project、task、model-template pin 和 correlation ID。
- Child Run 必须携带 parent_run_id、root_run_id、单调递增的 delegation_depth 和 Agent ancestry。
- Agent 必须在 tenant-bound ExpertRegistry 中可见、已启用，并且属于显式 allowed_agent_ids。
- 工具集合必须同时满足 DelegationPolicy 的 allowlist 和 Agent 已发布定义。Task Center 的运行图使用 Run-pinned Agent 完整工具集合，因此请求工具集合必须与发布定义精确相等；不能仅靠缩小请求字段来声称限制了图的实际工具能力。
- actor 由宿主从已认证 parent Run 上下文解析；不能由委派任务输入自行指定。
- Child Run ID 和 idempotency key 对 tenant + parent Run + delegation key 稳定，重复提交不能创建第二个 Child Run。

## 资源与链路限制

`DelegationPolicy` 提供显式 Agent/tool allowlist、最大深度、每次 delegate 调用的最大 fan-out、单子任务超时上限和最大回传文本长度。默认策略应由平台装配层显式配置，不得使用空 Agent allowlist。

深度从 0 开始；达到 max_depth 时拒绝继续委派。祖先 Agent 再次出现时按循环拒绝。一次批量委派的 delegation_key 必须唯一；fan-out 超限在创建任何 Child Run 前拒绝。

## 失败与接管语义

- 不自动把失败 Child Run 重试为另一个 Agent，也不自动切换模型/工具集合；当前自动失败接管次数为 **0**。
- 任一子任务失败、超时或回传结果超限，整批委派失败；取消并等待所有未完成兄弟任务退出，然后把原始失败交还 parent。
- 持久化执行器在 parent 取消时必须取消对应 Child Run；不能把子 Run 留作脱离 parent 的后台任务。
- parent 可以按其自身工作流显式处理失败或决定后续步骤；任何新委派仍受 allowlist、depth、cycle、fan-out、timeout 和稳定幂等键约束。该行为不是隐式 fallback。

## 回传契约

成功结果包含 delegation_key、child_run_id、agent_id、output_text、usage 和 trace_id。输出文本超过策略上限时拒绝回传。媒体结果由 Task Center 执行器作为受授权的 Artifact 引用回传，不复制媒体字节。

## 验收重点

单测覆盖租户/项目/模型模板/Task/trace 继承、Agent 与工具权限、循环与深度、fan-out、幂等、子任务超时、输出大小、兄弟取消和 parent 取消。Task Center adapter 测试应验证持久提交、Run 状态轮询、独立 Worker 完成、失败清理和跨项目/工具范围伪造拒绝。
