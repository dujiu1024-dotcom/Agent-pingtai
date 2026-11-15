# 多专家工作流与 Blackboard 持久化实现说明

## 组件边界

- LangGraph 负责专家分派、并行执行、汇总、质疑/冲突路由和人工审批中断恢复。
- Harness/模型执行通过 `MultiExpertCallbacks` 注入；该适配器不自研 Agent 执行循环。
- `BlackboardMultiExpertServices` 将工作流服务输出写入 `BlackboardRepository`，生产环境使用绑定租户的 PostgreSQL Repository。
- PostgreSQL Repository 的 `propose` 在同一事务内写入 Blackboard Entry、事件和 Outbox；事件由既有 Outbox Relay 至 Redis Streams。

## 持久化节点

| 工作流阶段 | Blackboard Entry |
|---|---|
| 专家输出 | CLAIM，记录专家 ID、输出和来源引用 |
| 初始综合 | ARTIFACT，引用各专家 CLAIM |
| 冲突发现 | CONFLICT，记录冲突描述和证据引用 |
| 冲突裁决 | DECISION，记录结果、理由和证据引用 |
| 冲突后综合 | ARTIFACT，引用冲突与裁决记录 |
| 人工审批请求 | NOTE |
| 人工审批结果 | DECISION，记录通过/拒绝 |
| 最终归档 | ARTIFACT，引用专家输出、综合结果、冲突、裁决和审批结果 |

## 幂等与隔离

- Entry ID 由 run_id + 阶段键通过 UUID5 确定性生成；LangGraph 节点重试不会重复创建同一阶段的 Entry。
- 唯一写入遇到并发冲突时，适配器重新读取既有 Entry；只有确认记录已存在才抑制写入异常。
- 工作流回调上下文统一注入 tenant_id、task_id、run_id；适配器在调用模型/专家回调前验证租户。
- Blackboard Repository 由服务端按 tenant_id 绑定，Agent 不接触数据库连接。
- 最终产物保存完整来源 Entry ID，便于从结果反查专家输出、冲突、裁决和人工审批。

## 验证

- `tests/test_blackboard_multi_expert_services.py` 覆盖候选 Entry、冲突、裁决、审批、归档及幂等重试。
- `tests/test_multi_expert_workflow_integration.py` 增加 LangGraph + Blackboard Adapter 集成用例，验证并行专家执行、冲突裁决、人工中断恢复和最终产物血缘。
- 仍需在 PostgreSQL 集成环境验证该适配器与真实 `PostgresBlackboardRepository` 的事务、Outbox 和租户隔离；测试替身不能替代生产验收。
