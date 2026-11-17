# C-13-01 专家注册、能力声明与任务分派协议

## 范围

本交付只覆盖专家元数据解析、能力声明校验和任务分派协议，不实现 Blackboard 条目 API、冲突裁决、事件总线或跨实例接管。

## 注册与能力声明

- 专家以租户作用域 Control Plane Registry 中已发布且启用的 Agent 定义为准；草稿、禁用或不存在的定义不可分派。
- `capabilities` 必须是字符串数组；分派要求的 `required_capabilities` 必须是非空字符串集合，且必须是专家已声明能力的子集。
- 不从 Registry 元数据加载可执行对象或凭据；Harness/Gateway 仍负责运行时组装和授权。

## 分派协议

每个 assignment 包含稳定的 `assignment_id`、`task_id`、`expert_id`、`focus`、`required_capabilities` 和 `declared_capabilities`。同一 task/run/expert 重试得到相同 assignment ID。运行节点再次读取 Registry，拒绝任务作用域不匹配、专家被撤销/禁用或能力声明在规划后发生变化的 assignment。

## 并行与共享上下文

LangGraph 的 Send fan-out 继续并行执行多个 assignment；每个 worker 接收同一任务的 tenant/task/run 上下文和独立 assignment。专家输出由现有 Blackboard services 持久化，后续汇聚流程保持原有实现。本任务不改变该流程。

## 验证边界

新增单测覆盖 task 绑定、稳定 assignment ID、能力缺失、专家不存在和规划后能力变更。CI 在 PR 上执行；真实 Agent 模型、生产 Registry 与消息基础设施验证按调度要求跳过。
