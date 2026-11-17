# 记忆分层与边界（C-11-01）

## 目标与硬约束

记忆不是一个可任意共享的“聊天摘要”。平台按所有权、可见范围、生命周期和持久化责任分层。任何层都必须先绑定 tenant；跨 tenant 共享不属于本任务定义的能力，不能通过组织共享层绕过租户隔离。

**运行短期记忆、会话记忆与长期治理记忆是不同的数据责任。** 前两者分别属于 Run 执行状态和会话历史；只有经过单独候选提取、授权与确认的内容，才可以进入长期记忆。不得把聊天全文、Run state、工具输出或会话历史直接复制进长期记忆。

## 六层边界

| 层 | 所有者 | 可见范围 | 生命周期与持久化 | 长期记忆映射 |
|---|---|---|---|---|
| 短期运行记忆（RUN） | 单个 Run | 当前 Run 及其获授权的子步骤 | 执行期间；必要状态由 Run checkpoint/recovery 管理 | 不映射，不进入 MemoryService |
| 会话记忆（SESSION） | 单个 tenant 内的会话 | 会话参与者及获授权访问者 | 会话生命周期与会话历史保留策略 | 不映射，不进入 MemoryService |
| 用户长期记忆（USER） | tenant 内单个用户 | 用户本人；治理操作另需授权 | 持久化，直至纠正、遗忘、过期或策略清理 | `MemoryScope.USER` |
| Agent 记忆（AGENT） | tenant 内单个 Agent | 使用该 Agent 的获授权 Run；管理需 Agent 级授权 | 随 Agent 生命周期治理，可归档 | `MemoryScope.AGENT` |
| 项目记忆（PROJECT） | tenant 内单个项目 | 项目成员且具备资源访问权的主体 | 随项目生命周期治理，可归档 | `MemoryScope.PROJECT` |
| 组织共享记忆（ORGANIZATION） | 一个组织安全边界；当前由一个 tenant 表示 | 该 tenant 内被授权访问组织共享记忆的主体 | 受组织治理与保留策略约束 | `MemoryScope.TENANT` |

## 读写规则

1. RUN 层只服务于当前执行上下文；不能因 Run 完成、重试或子 Run 创建而自动变成用户长期记忆。
2. SESSION 层以会话历史为事实来源；历史消息本身不是长期记忆，也不能因跨轮可见而扩大到用户、Agent、项目或组织范围。
3. USER 层不得被其他用户通过同 tenant 身份自动读取；管理访问仍须经过授权审计。
4. AGENT / PROJECT 层是不同资源所有权，不互相继承；成员可读不等于成员可管理或可写。
5. ORGANIZATION 层不是“全平台共享”。当前 `MemoryScope.TENANT` 的安全边界是 tenant；组织跨 tenant 时必须另行定义显式组织身份、成员资格、授权和审计契约，禁止把 tenant_id 省略或当作全局组织 ID。
6. 对 USER、AGENT、PROJECT、ORGANIZATION 的长期写入必须先形成带来源引用的候选，按对应范围授权并由授权主体确认后才可检索。候选确认、冲突、版本、过期和删除传播的具体工作流由 C-11-02～C-11-05 负责，本任务不重复实现。

## 与相邻数据域的责任边界

- **Run state / checkpoint**：恢复执行所需的短期状态与步骤状态；不是长期记忆。
- **Session / message history**：原始对话及会话事件的权威记录；记忆只引用来源，不复制整段历史。
- **Knowledge Base / RAG**：文档、外部知识和可引用证据；不承担用户偏好或 Agent 经验的个人化记忆责任。
- **Blackboard**：当前任务内专家/节点协作的共享工作区；任务结束后不自动成为项目或组织记忆。

## 当前实现映射

现有 `agent_platform.memory.service.MemoryScope` 已提供 USER、AGENT、PROJECT、TENANT 四种持久化范围，并由 `MemoryPrincipal` 执行 tenant、user、Agent、project 与管理权限过滤。现有 `MemoryExtractionService` 只接收单次交互 turn，输出候选而非直接激活记忆。新增 `agent_platform.memory.boundaries` 将六层语义集中定义；RUN/SESSION 明确不属于长期 MemoryService，ORGANIZATION 在当前模型中受限映射到 tenant-scoped TENANT。

本定义不声称完成真实跨 tenant 组织共享，也不引入独立的 Run/Session 存储实现；它明确各自归属，防止把不同数据域混写。
