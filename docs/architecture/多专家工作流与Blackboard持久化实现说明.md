# 多专家工作流与 Blackboard 持久化实现说明

## 组件边界

- LangGraph 负责专家分派、并行执行、汇总、质疑/冲突路由和人工审批中断恢复。
- Harness/模型执行通过 `MultiExpertCallbacks` 注入；该适配器不自研 Agent 执行循环。
- `ExpertRegistry` 提供专家能力目录契约，分派结果可校验专家是否已注册、启用及满足所需能力；生产适配器应连接控制面 Agent/Skill 目录。
- `BlackboardMultiExpertServices` 将工作流服务输出写入 `BlackboardRepository`，生产环境使用绑定租户的 PostgreSQL Repository。
- `create_blackboard_router` 提供条目写入、查询、读取、质疑、裁决和版本修订 API；宿主必须注入身份解析、租户绑定 Repository 工厂和作用域授权器。
- API 对新写入来源引用调用宿主 `verify_evidence_refs`；有引用但未配置验证器时返回 503，引用缺失或不可访问时返回 422；通过验证后仅持久化去重、去空白的引用。生产宿主需将验证器连接到知识库/文档权限服务，不能以字符串格式检查替代真实校验。
- PostgreSQL Repository 的 `propose` 在同一事务内写入 Blackboard Entry、事件和 Outbox；事件由既有 Outbox Relay 至 Redis Streams。

## 持久化节点

| 工作流阶段 | Blackboard Entry |
|---|---|
| 专家输出 | CLAIM，记录专家 ID、输出和来源引用 |
| 初始综合 | ARTIFACT，引用各专家 CLAIM |
| 冲突发现 | CONFLICT，记录冲突描述和证据引用 |
| 证据质疑 | CHALLENGE，记录质疑原因、冲突记录和证据引用 |
| 冲突裁决 | DECISION，记录结果、理由和证据引用 |
| 冲突后综合 | ARTIFACT，引用冲突、质疑与裁决记录 |
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

- `tests/test_blackboard_multi_expert_services.py` 覆盖候选 Entry、冲突、裁决、审批、归档、幂等重试及专家能力匹配/禁用校验。
- `tests/test_multi_expert_workflow_integration.py` 验证 LangGraph + Blackboard Adapter 的并行专家执行、冲突裁决、人工中断恢复和最终产物血缘。
- `tests/test_platform_postgres_integration.py` 增加真实 PostgreSQL Repository 工作流集成用例，覆盖事务 Outbox 和租户隔离；该用例仍须由 CI 实际运行通过，不能仅凭代码存在视为验收完成。
- `tests/test_blackboard_api.py` 覆盖 HTTP API 的读写、质疑、裁决、版本冲突、作用域越权、证据校验失败及验证器未配置时的 fail-closed 行为。
