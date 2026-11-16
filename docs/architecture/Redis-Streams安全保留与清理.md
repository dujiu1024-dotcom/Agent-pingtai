# Redis Streams 安全保留与清理

## 为什么不能在 XADD 时按 MAXLEN 截断

Redis Stream 的近似 MAXLEN 裁剪可能删除仍处于 Consumer Group Pending Entries List（PEL）的消息体。PEL 仍保留 message ID，但后续消费者无法再读取已裁剪的 payload，破坏 at-least-once 恢复语义。DLQ 同样不能在写入时无条件裁剪未处理的死信。

## 当前实现

- 正常 Outbox 发布和 DLQ 写入不再设置 MAXLEN。
- 新增 `RedisStreamsTransport.trim_acknowledged`，只按所有 Consumer Group 的安全水位裁剪。
- 每个 Group 的安全水位取其最早 pending ID；若无 pending，则取 last-delivered-id。
- 全局裁剪水位取所有 Group 水位的最小值；XTRIM MINID 只删除严格早于水位的消息，保留 pending 和未投递消息。
- 没有 Consumer Group、Group 水位不可确认或存在未投递起点时，不执行裁剪。
- DLQ 使用相同规则，调用时传 `dead_letter=True`。

## 运维约束

该实现不自动安排清理任务。部署方应按存储容量与消费者延迟配置维护任务，先检查 pending/lag，再调用安全裁剪；不得恢复为发布时固定 MAXLEN。多 Consumer Group 场景必须让所有 Group 都纳入水位计算。

## 验收

Redis 集成测试验证：pending 消息不被裁剪、已 ACK 的前序消息可被安全裁剪、尚未创建 Consumer Group 的消息不会被裁剪。生产环境仍需演练长时间消费者离线、多个 Group 不同进度、DLQ 消费者故障和存储容量告警。
