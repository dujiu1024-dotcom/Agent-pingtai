# Harness 选型与隔离方案

- 日期：2026-11-15
- 状态：候选评估，未冻结唯一实现
- 目标：复用成熟 Agent 执行循环，不在平台内重造 Harness。

## 1. 先区分两层

- **LangGraph**：负责平台级流程编排、状态持久化、分支、暂停/恢复和人工介入。
- **Harness**：负责单个 Agent 的执行回合、上下文组装、模型请求、工具调用循环、流式输出、停止条件与运行生命周期。

两者不是替代关系。LangGraph 节点可调用一个 Harness 运行单次 Agent 任务；Harness 不拥有租户、项目、黑板和审批的最终事实。

## 2. 候选对比

| 候选 | 形态 | 优势 | 风险/限制 | 本项目定位 |
|---|---|---|---|---|
| DeepSeek Harness（dsh） | TypeScript/Node 运行时；提供 headless、SDK、ACP 等 Profile，并有 Python SDK 启动 SDK Profile | 插件化；模型、工具、会话、Agent Loop 都可替换；MIT；官方项目 | Developer Preview；兼容性可能变化；官方声明未经过安全审计，具备代码/命令执行、文件、进程、网络和凭据访问能力 | 第一候选 PoC；仅隔离执行服务，不直接作为平台安全边界 |
| Strands Agents SDK | Python / TypeScript SDK | 进程内 SDK；工具、MCP、多 Agent、流式、生命周期控制、Guardrails、Tracing、Evals 等能力齐全 | 需验证运行恢复、审批接入和长期会话持久化是否满足平台要求 | Python 原生备选 |
| LangGraph | Python 工作流/Agent 图编排 | 状态化流程、分支、检查点、人工介入 | 不等同于完整 Harness；不应自行承担工具安全网关 | 平台工作流主编排器 |

## 3. DeepSeek Harness 的集成判断

官方项目说明其核心采用 Everything-is-a-Plugin 架构，包含 headless、SDK、ACP 等 Profile。官方 Python SDK（`deepseek-harness-sdk`）通过 stdio 上的 newline-delimited JSON-RPC 启动匹配版本的 `dsh` 子进程；调用方必须显式提供 Harness home 和 workspace，正常 SDK 运行不需要系统 Node.js。该模式适合将 dsh 放在独立进程/服务中，由平台通过适配器调用，而不是把其内部实现复制进 Python 单体。

官方仓库当前标记为 Developer Preview，且官方安全声明明确表示尚未经过安全审计；它能够执行模型生成的代码和命令、加载第三方插件并访问被授予的文件、进程、网络和凭据。沙箱和审批不能被视为绝对隔离。

因此本项目采用以下边界：
1. dsh 只在独立执行容器/虚拟机运行，使用专用低权限账号；容器必须限制读取范围，而不只是限制写入范围。
2. 不挂载平台数据库、Redis、宿主机凭据目录或客户资料根目录。
3. 网络默认拒绝，只允许访问受控模型代理；代理在容器外持有真实供应商密钥并实施模型、租户、额度和审计策略。
4. MCP 工具必须经过平台 Tool Gateway；不得把平台数据库或高权限凭据直接注册给 dsh。当前 DSH 适配器在 Gateway-only Profile 验证前拒绝所有平台工具请求。
5. 文件访问只开放一次性任务工作区；任务完成后销毁或归档。
6. 资金、权限、对外发布等 L2/L3 操作仍由平台审批策略控制，不能依赖 dsh 自身审批弹窗。
7. dsh 插件必须固定版本、审查源码和依赖许可证；未经审查不允许加载社区插件。
8. 任何运行产物、工具调用和审批记录回传平台，平台才认定任务完成。

当前代码已增加 `DeepSeekHarnessAdapter` 实验适配器：通过官方 Python SDK 调用 dsh；默认只接受显式隔离环境、read-only 权限和无平台工具请求。非 DeepSeek Provider 必须使用经过审查的 SDK Profile patch；平台工具调用在 MCP Gateway Profile 验证完成前一律拒绝。该适配器目前只用于 PoC，不代表生产可用。注意：DSH 的 `read-only` 权限模式只约束部分写入行为，不构成文件读取隔离或网络隔离；官方讨论明确说明读取和网络访问仍可能开放。因此 `container_isolation_verified` 必须代表外部容器已做到最小挂载、文件系统隔离、网络仅允许访问模型代理、禁止宿主机凭据和 Docker Socket。Provider 环境变量只能包含指向代理的地址与短时、限额的代理令牌，真实供应商密钥必须留在代理侧。

## 4. PoC 验证矩阵

必须完成以下测试后，才能决定是否采用 dsh：
- 无头启动、SDK 调用和停止/取消。
- Python 平台到 dsh 的请求/响应协议、超时和错误映射。
- OpenAI-compatible 通用模型供应商接入，不绑定 DeepSeek 模型。
- MCP 工具发现、调用、参数校验和拒绝路径。
- 工具调用是否能被平台外部网关完全拦截。
- 会话/运行状态落盘、进程崩溃后的恢复和幂等。
- 流式事件、Token 用量、调用轨迹能否完整回传。
- 沙箱文件系统、进程、网络和凭据隔离验证。
- 插件安装/卸载、版本锁定、供应链和许可证审查。
- 至少 20 条固定任务评测：正确性、工具成功率、越权拒绝率、恢复成功率、成本与时延。

## 5. 当前决定

- LangGraph：平台工作流主编排器。
- DeepSeek Harness：第一候选执行引擎，先做隔离 PoC，不直接进入生产信任域。
- Strands Agents：Python 原生备选，若 dsh 集成/隔离成本过高则评估。
- 自研范围：只做平台控制面、黑板、权限/审批、适配器和业务治理；不自研模型工具调用循环。

## 6. 官方资料

- DeepSeek Harness：<https://github.com/deepseek-ai/deepseek-harness>
- DeepSeek Harness Python SDK：<https://github.com/deepseek-ai/deepseek-harness/tree/master/python/sdk>
- Python SDK 入门：<https://deepseek-harness.github.io/deepseek-harness/guide/python-sdk>
- DeepSeek Harness Architecture：<https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.md>
- DeepSeek Harness Safety：<https://github.com/deepseek-ai/deepseek-harness/blob/master/SAFETY.md>
- Strands Agents：<https://github.com/strands-agents/strands-agents>
