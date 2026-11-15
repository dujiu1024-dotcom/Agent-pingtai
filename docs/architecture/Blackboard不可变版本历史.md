# Blackboard 不可变版本历史

Blackboard 采用 Hybrid 模式：当前状态可被查询，事实、主张、证据、质疑、冲突和决策以追加记录保留。条目修订不覆盖旧版本，而是写入相同 entry_id 的新 version。

## API

`GET /api/platform/v1/blackboard/entries/{entry_id}/history`

- 先解析认证主体，再通过条目所属 scope 执行 read 授权。
- 仓储按 tenant_id + entry_id 查询，按 version 升序返回。
- 返回字段与单条目 API 一致，调用方可重建该条目的变更序列。
- 不支持版本历史的仓储明确返回 501，不回退到当前版本伪造历史。

## 持久化

PostgreSQL 的 `blackboard_entries` 保留每个版本；新增仓储端口 `list_versions` 只读读取该版本链。事件与 outbox 仍由既有事务写入机制负责，不改变 Hybrid Blackboard 的决策模型。

## 验收限制

本次补齐单条目版本链查询及 API 授权边界测试。完整多专家并行、冲突裁决、审批、归档已有工作流服务和测试覆盖；生产消息总线回放、跨实例故障演练仍需 CI/部署环境验收。
