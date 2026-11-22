# P-UI-001 全局交付协调与验收状态

> 对应总控：[#742](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/742)  
> 核验日期：2026-11-22  
> 基线：GitHub 当前 `main`，HEAD `b397db499e96b604d02acbca14c9a99d982b9185`  
> 本文是交付协调/证据核验，不替代各子 Issue 的功能 DoD，也不把 PR merged 等同于产品验收通过。

## 1. 执行结论

**P-UI-001 尚未达到全局验收完成。** 当前可确认多项代码切片已进入 main，但仍存在开放子 Issue、未合并 PR、真实 API/权限契约缺口、浏览器/集成验收未执行或失败，以及状态记录滞后。

本轮核验没有修改业务实现、没有合并 PR、没有关闭 Issue。新增本报告作为总控的可追踪状态基线。所有判断以 GitHub 当前 PR/Issue 元数据和对应 Issue 评论为准；历史评论中的旧 PR 状态不覆盖当前元数据。

### 核心结论

1. Gate 0 页面地图：PR #789 已合并，页面地图契约专属测试评论记录 10/10 PASS；但该 PR 的 aggregate CI 有既有 Harness lint/core acceptance 失败，因此不能据此宣称全仓 CI 通过。
2. 工程基座和双模式前端切片：PR #908、#910、#913 已合并；但 #743 仍 OPEN，服务端权限词汇、生产 WebSocket 契约及外部 OIDC/SAML 等能力仍有明确缺口。
3. 核心看板：PR #917/#919/#928/#930 已合并；#744–#747 仍 OPEN，子 Issue 评论明确列出指标字段、真实 API、运行事件、下钻、响应式/无障碍和 CI 缺口。
4. 3D Office：PR #904 已合并；仍有 10 秒快照轮询而非 SSE/WebSocket 实时推送、空间布局持久化等未验收项。真实设备验证不在本轮范围。
5. 管理后台：PR #923/#927/#924 等已合并，但 #749/#750 仍 OPEN。组织/RBAC 写 API 与审计契约缺失；Workflow 设计器的服务端 Node Registry、Schema/端口校验、LangGraph 编译及不可变发布契约缺失；会话/文件等模块仍有单独未完成项。
6. 集成切换：PR #933 已合并，但 #752 仍 OPEN；跨模块双模式、完整浏览器 E2E、REST/SSE/文件权限矩阵、灰度和回滚演练不能凭合并记录判定完成。
7. Round 3 新增任务：#814、#815、#816、#818 仍 OPEN；PR #936/#939/#940/#941 均 OPEN 未合并。旧 PR #820/#822/#823/#824/#825/#841 均 CLOSED 且未合并，不计为 main 交付。#817 已关闭且对应 PR #904 已合并。

## 2. 主工作包状态矩阵

| 工作包 | Issue 当前状态 | 当前可核实的 main 交付 | 尚未满足/风险 | 总控判断 |
|---|---|---|---|---|
| A 工程基座 | #743 OPEN | #908、#910、#913 merged | PR 评论记录的 API/权限词汇、WebSocket 生产握手/续传和 SSO 契约缺口；aggregate CI 既有失败 | 部分完成 |
| B 运营驾驶舱 | #744 OPEN | #917 merged | 吞吐/成功率序列、失败 TopN、队列、Agent 健康、P50/P95/TTFT 等数据缺口；字段口径/权限/下钻与 CI 未闭合 | 部分完成 |
| C 任务指挥中心 | #745 OPEN | #919 merged | 任务状态机/动作权限、事件乱序/断线恢复、双模式及完整 E2E 未全部证实；CI 曾因 Playwright/Vitest 收集及仓库基线失败 | 部分完成 |
| D Agent 协作 | #746 OPEN | #928 merged | Agent 团队关系与节点事件契约不完整；仅展示实际事件；CI 曾有 Vitest/Playwright、Mypy、Ruff、Identity、集成失败 | 部分完成 |
| E 成本与质量 | #747 OPEN | #930 merged | P50/P95/TTFT 等 API 缺口；评测数据集详情归 #750；no-auth 测试执行证据不完整；CI 多项失败 | 部分完成 |
| F 3D Office | #748 OPEN | #904 merged | 10 秒快照轮询；缺少 SSE 实时状态和空间布局持久化契约；现场/设备验收待后续部署条件 | 部分完成 |
| G 通用管理后台 | #749 OPEN | #783/#776/#781/#923/#927 merged | 组织/RBAC 生命周期写 API/审计缺失；Workflow 服务端图校验、编译、LangGraph 发布契约缺失；不能把只读页/草稿编辑当完整 CRUD/发布 | 阻塞未闭环 |
| H 业务管理后台 | #750 OPEN | #924 merged；#760/#761/#763 closed | #762 仍 OPEN；会话/文件附件、上传/下载/range、双模式和真实存储验收仍需证据；Hub 只负责聚合入口 | 部分完成 |
| I 搜索/通知/审计/运维 | #751 CLOSED | #913 merged（搜索/通知交互部分） | 历史评论已说明搜索/通知后端和审计/运维完整能力未证实；关闭状态不能代表全部验收 | 状态需复核 |
| J 集成/切换/回滚 | #752 OPEN | #794、#933 merged | 完整跨模块 E2E、双模式权限矩阵、浏览器验收、灰度监控与真实回滚演练仍未证明 | 未完成 |
| K 页面地图 | #753 CLOSED | #789 merged | 页面地图专属 10 项测试通过；aggregate CI 仍有与本 PR 无关的失败；需随新页面变化持续维护 | 基线完成，持续维护 |
| L 后端双模式 | #755 OPEN | #926、#929 merged；main 另有 no-auth 基础修复 | Issue 仍 OPEN；必须核实完整 REST/SSE/文件双模式覆盖及异常切换，而非只看配置/回归切片 | 部分完成 |

## 3. G/H 工作域子 Issue

| 子 Issue | 当前状态 | 已有合并切片 | 仍需注意 |
|---|---|---|---|
| #756 组织/成员/RBAC | CLOSED | #783 merged | 只读展示不等于组织、成员、角色、数据范围 CRUD；#749 评论已确认写 API/审计契约缺失 |
| #757 Agent Definition | CLOSED | #776 merged | 版本详情/diff 已有；仍需按 #749 检查引用校验、权限审计、并发和双模式 |
| #758 Workflow 设计器 | OPEN | #927 merged | 可视化草稿能力已有；服务端 Node Registry、Schema/端口校验、LangGraph compile/diagnostics 和 immutable publish 未实现/未证实 |
| #759 Model/Provider/Tool/Skill | OPEN | #781、#923 merged | Provider/Model 已由 #781 合入；Tool/MCP 连接测试/健康检查、Skill 兼容评估 API 缺失 |
| #760 Knowledge | CLOSED | 需结合 main 及 PR 元数据持续核验 | 关闭状态不是字段/API/ACL/导入导出/版本完整验收证据 |
| #761 Memory | CLOSED | 需结合 main 及 PR 元数据持续核验 | 关闭状态不是完整生命周期/版本/权限验收证据 |
| #762 Sessions/Files | OPEN | 历史 #784 CLOSED 未合并；另有近期基础资产 PR 需按文件/契约核对归属 | 会话/消息/附件/文件版本/下载/恢复/删除/权限和双模式 E2E 仍需逐项确认 |
| #763 Approval/Evaluation | CLOSED | 需结合 main 及 PR 元数据持续核验 | 审批决策权限、评测 Dataset/Case/Run 详情、导出和审计闭环需按 DoD 核对 |

## 4. Round 3 页面与集成 PR 状态纠偏

### 当前仍有效的活动 PR

| Issue | PR | 当前 PR 状态 | 说明 |
|---|---:|---|---|
| #815 | #936 | OPEN / unmerged | 仅接通现有 KPI Dashboard 到工作台路由；CI 曾出现 Unit tests/Lint 失败，typecheck/build 通过，浏览器 E2E 未完成 |
| #797 | #939 | OPEN / unmerged | Operations dashboard canonical route；非 #742 原始子 Issue，但属于当前工作台路由集成 |
| #798 | #940 | OPEN / unmerged | RBAC canonical route；页面/路由接线切片，不代表 RBAC CRUD 完成 |
| #809 | #943 | OPEN / unmerged | 登录与管理员初始化页及初始化 API 接线；CI 当时排队，待当前 head 终态 |
| #812 | #947 | OPEN / unmerged | 系统设置只读接线；CI 当时排队，待当前 head 终态 |
| #818 | #941 | OPEN / unmerged | 侧栏、顶部栏、路由守卫和 Playwright 浏览器用例；已有 focused-unit 失败，整体 CI fail |

### 历史 PR 不能作为交付的记录

PR #820、#822、#823、#824、#825、#841 当前均为 CLOSED 且 `merged=false`。它们的历史说明和分支代码不等于 main 上的实现，也不能用来关闭 #814–#818。PR #904 当前已 MERGED，作为 #817 Office 实现切片计入 main；但不能把它扩张解释为整个 #814 Round 3 总控完成。

## 5. CI 与质量基线

当前不能给 P-UI-001 总控标记 aggregate CI PASS。多个工作包的历史 CI 记录反复出现以下仓库级问题：

- **Mypy**：`agent_platform/observability/run_trace.py` duplicate module；属于全仓类型检查基线，阻断相关 PR 的 Type check。
- **Ruff**：`tests/test_structured_output.py` 有约 74 个 F811 duplicate definitions；阻断相关 Lint。
- **Frontend Vitest/Playwright 隔离**：历史多个 run 将 `frontend/e2e/*.spec.ts` 当作 Vitest 单测收集，报 `Playwright Test did not expect test()`；另有 `task-model.test.ts` 调用缺失的 `normalizeTaskListColumns` 导出。PR #933 后已调整 Vitest include 与 E2E job，但必须以当前 main/head 的最新 CI 实际执行结果为准。
- **Identity / integration**：历史 CI 记录中 file asset tenant isolation 返回 405（预期 200）、PostgreSQL/Redis 集成失败，以及 release-gate focused tests 的既有配额/报告断言失败。
- 安全扫描（security-unit / dependency-security / dependency-audit）在多份记录中通过，但不能替代类型检查、构建、浏览器和集成验收。

注意：本次调度标题中的“CI baseline A：修复 Mypy 61 个类型错误”与 Issue #742 正文的实际任务清单不一致。#742 正文定义的是 P-UI-001 前端全面重构总控协调；本次按 Issue 正文执行，没有把 Mypy 全仓修复擅自并入 #742，也没有缩减类型检查范围。Mypy 基线应由其独立质量治理 Issue 负责。

## 6. 全局 DoD 缺口

以下项目在当前证据集中仍不能判定为全局通过：

1. 所有页面的字段字典与真实 API/JSONPath、单位、枚举、空值、聚合口径、刷新策略、权限和下钻矩阵。
2. 各模块 auth=false/auth=true 核心路径冒烟；#752 负责的跨模块权限穿透矩阵。
3. REST、SSE/WebSocket、文件下载/range/附件的授权一致性、断线续传和快照补偿。
4. 组织/RBAC 管理写 API、审计和数据范围授权的服务端闭环。
5. Workflow 定义服务端 Schema/端口校验、LangGraph 编译、不可变发布与运行态关联。
6. 搜索索引、通知投递/已读同步、审计保留/不可变性、运维诊断 API 的端到端契约。
7. Home/Office/各模块 Playwright 在 CI 中实际执行并通过；真实截图/视觉回归不能以组件存在或测试文件存在代替。
8. 桌面/平板/手机降级、键盘可访问性和 WCAG 2.2 AA 适用项证据。
9. 新旧并行部署、灰度监控门槛、回滚演练；真实部署验收需由有权限的部署环境单独执行。
10. PR merge 状态、Issue open/closed 状态和 #267/#884 台账同步；不得用关闭状态代替 DoD。

## 7. 后续协调顺序（不在本轮并行开工）

1. **先完成活动 PR 的 main 集成决策**：#936/#939/#940/#943/#947/#941 逐个审查 CI 终态和依赖关系；由维护者决定合并，不在本总控文档 PR 中代替维护者合并。
2. **先解决质量门禁基线**：独立修复 Mypy duplicate module、Ruff F811、Vitest/Playwright 测试收集和 task-model export；保留完整 Mypy 检查范围，不通过缩减检查文件数规避。
3. **G 域后端契约**：先补组织/RBAC 写 API/审计和 Workflow Node Registry/compile/publish 契约，再验收前端完整生命周期。
4. **H 域与 I 域核验**：逐项对照当前 main 核实 #762、#751 的关闭/开放状态与实际功能，必要时由维护者重开或修订，不创建重复实现。
5. **#752 系统 Gate**：在各模块 PR 集成后执行跨模块 Playwright、双模式权限、REST/SSE/文件、灰度与回滚；没有真实环境证据的项目继续标记待验证。
6. **总控收口**：更新 #267/#884 证据台账；所有子 Issue 的验收证据齐备后再关闭 #742。

## 8. 核验范围与限制

- 已读取 #742 正文、#742 评论，以及 #743–#763、#814–#818 和相关 Round 3 Issue/PR 记录的关键评论/元数据。
- 已核验 PR 当前元数据：#908/#910/#913/#917/#919/#928/#930/#904/#927/#923/#924/#933 等为 merged；#936/#939/#940/#943/#947/#941 为 open、unmerged；#820/#822/#823/#824/#825/#841 为 closed、unmerged。
- 本报告不是对仓库所有 Issue 的全量审计，也不是部署环境验收；没有申请真实服务器或外部资源，真机验证跳过。
- 本轮仅交付协调报告和证据索引，不修改产品代码，不合并 PR，不关闭子 Issue。
