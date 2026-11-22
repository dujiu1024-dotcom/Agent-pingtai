# 需求覆盖审计归档总索引

- 仓库：`dujiu1024-dotcom/Agent-pingtai`
- 归档目的：将 #884 需求覆盖审计、Issue inventory 与 main 实现去重证据集中归档；本目录不承接产品功能实现。
- 任务入口：[#884](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/884)
- 本次归档基线：main `b397db499e96b604d02acbca14c9a99d982b9185`（2026-11-22）
- 历史阶段 PR：[#890](https://github.com/dujiu1024-dotcom/Agent-pingtai/pull/890) 已关闭且 `merged=false`，其内容未进入 main；本次将其三个文档成果以审计归档形式纳入本分支，不把 PR #890 视为交付或合并。

## 归档文档

| 文档 | 责任范围 | 使用规则 |
|---|---|---|
| [REQUIREMENTS_COVERAGE_AND_ISSUE_TRACEABILITY.md](./REQUIREMENTS_COVERAGE_AND_ISSUE_TRACEABILITY.md) | 原始需求 → 原子 Requirement → 领域 Issue 的需求覆盖矩阵、补漏方向和唯一归属 | 代表性需求映射与历史补漏记录；不能单独证明全量原子需求 100% 覆盖 |
| [ISSUE_INVENTORY_AUDIT_2026-11-21.md](./ISSUE_INVENTORY_AUDIT_2026-11-21.md) | 99 个 Issue 的正文/评论库存审计、状态漂移、父子关系、潜在重叠和验收弱项 | 以 2026-11-21 inventory 查询快照为准；历史评论不作为当前 CI 状态 |
| [MAIN_IMPLEMENTATION_CLEANUP_2026-11-22.md](./MAIN_IMPLEMENTATION_CLEANUP_2026-11-22.md) | main 代码路径、测试文件、PR merge 状态、workflow 结果及剩余范围 | 只用于识别已合入切片和避免重复开发；不把测试文件存在等同测试通过 |

## 统一映射与唯一归属规则

| 需求/能力域 | 需求/治理入口 | 唯一实施归属（当前已识别） | 不重复建设的边界 |
|---|---|---|---|
| 会话、消息、文件资产与连接器基础服务 | #884 需求追踪；#848 战略基线 | #8 | 已合入代码切片从活跃开发移除；只补明确剩余 API/契约/验收 |
| Task/Run、Worker、调度、重放与执行恢复 | #848 / #884 | #9 | CI 基线 #882 不替代运行时功能验收 |
| 知识检索、RAG、ACL、记忆来源契约 | #884 | #10；记忆治理生命周期归 #11 | #887 只负责知识资产治理，不复制 RAG/检索运行时 |
| 记忆治理与生命周期 | #884 | #11 | 不将记忆写入/删除/纠错与知识库检索混为同一实现 |
| 生产组合根、Agent Runtime、Tool Gateway、模型路由 | #848 | #12 | #881/#883 是 CI/type 基线，不是 Runtime 功能 owner |
| 多专家协作、Blackboard、仲裁和恢复 | #848 | #13 | 前端/运营展示与协作引擎语义分离 |
| Workbench 用户流程与 E2E | #742 | #14 | 页面/交互验收不替代执行引擎 |
| 身份、租户、授权、审批、审计和策略 | #848 ADR / #884 | #15 | #888 的供应链签名/漏洞准入独立于通用身份授权 |
| 可观测、告警、追踪和运营指标 | #848 / #742 | #16 | #747 成本质量中心复用指标来源，不重造遥测底座 |
| 配额、成本、Token 与质量指标 | #742 | #17 / #747（按运行服务与产品分析边界） | #886 仅扩展业务价值归因/ROI，不重复 Token 计量 |
| 部署、迁移、备份、恢复、HA/DR | #848 | #18 | #889 专注混合部署离线自治与连续运行，不重做通用备份底座 |
| 企业价值/ROI | #884 | #886 | 与 #747 成本/Token 计量分开 |
| 数字员工任职与生命周期、团队组织 | #848 | #870 | 不新建平行数字员工目录/生命周期 Issue |
| 跨 ERP/CRM/OA/MES 业务闭环 | #884 | #885 | 复用 #8/#9/#12/#15 的连接器、执行、身份基础；业务流程 owner 仍唯一为 #885 |
| 知识资产责任、血缘、质量、时效、权限传播 | #884 | #887 | 不复制 #10 的 RAG/检索/向量索引 |
| 模型/Agent/Skill/Tool/Connector 供应链可信治理 | #884 | #888 | 复用 #20/#870 的目录与运行能力；签名/SBOM/许可证/撤销归 #888 |
| 混合部署离线自治与控制面降级 | #884 | #889 | 复用 #18 的部署/备份底座；离线授权、任务接管与自治验收归 #889 |
| 工作台与产品页面入口 | #742 | #742 及其子 Issue | 页面拆分项不重复实现后端服务 |
| 全量需求映射、重复治理与审计证据 | #884 | #884（本 Issue） | 审计 Issue 只维护映射，不实现产品能力 |
| 原子任务、PR/commit/CI/现场证据台账 | #267 | #267 | 台账不是产品实施 owner；勾选框不等于功能完成 |
| 战略代码差距与架构 ADR | #848 | #848 | 审查报告不替代领域实现 Issue |
| CI / 类型 / 数据库质量基线 | #881/#882/#883 | 对应 CI Issue | CI Issue 不替代 #8–#21 领域验收 |

## 去重与归档决定

1. PR #890 的三个审计文档曾只存在于未合并分支；该 PR 当前为 closed / merged=false，不能把它算作 main 交付。本分支重新以 `docs/audit/` 为唯一审计文档归档位置。
2. 同一领域只保留一个实施主 Issue。审计入口、执行台账、CI baseline 与产品实现分层，不创建平行实现任务。
3. #885–#889 是不同业务能力缺口，不与 #8–#21 的底层基础服务重复：跨系统业务闭环、ROI、知识资产治理、AI 供应链、混合部署自治分别归属上述五个 Issue。
4. #747 与 #886 分离：前者负责 Token/费用/质量计量，后者负责工时节省、收益归因和部门/岗位 ROI。
5. #10 与 #887 分离：前者负责知识检索/RAG，后者负责知识资产元数据、责任、血缘、质量、时效和影响分析。
6. #18 与 #889 分离：前者负责通用部署/备份/恢复，后者负责控制面不可达时企业侧自治、授权缓存、任务恢复和混合部署连续运行。
7. #870 是数字员工生命周期唯一入口；不再创建重复的数字员工目录/入职/考核/离职 Issue。
8. 已合并 PR 只移除其 diff 实际覆盖的代码切片；closed-but-unmerged PR 不计入交付；CI failure/cancelled/pending 与现场验收缺口继续保留。

## 统计口径与时间线

| 快照 | Open | Closed | Total | 解释 |
|---|---:|---:|---:|---|
| 2026-11-21 inventory 原始搜索 | 11 | 88 | 99 | 完成 99 个 Issue 正文和评论线程读取 |
| 2026-11-22 第一轮清理后 | 62 | 37 | 99 | 状态漂移候选重新开放 |
| 2026-11-22 最新记录 | 63 | 36 | 99 | #72 因剩余 CI/生产验收重开；以当日更新记录为准 |

原子任务计数采用 #267 去重后的最新记录：186 个唯一 C/A 编号，185 个已核验勾选，1 个未勾选（A-12-02 生产 OCI 隔离实测）。勾选表示核验登记状态，不自动代表功能、CI 或生产验收通过。

## 当前审计结论

- 主要产品能力已有战略入口和领域 Issue；重点补漏由 #885–#889 承接，数字员工生命周期归 #870。
- 尚不能宣称所有原子需求 100% 覆盖：完整的战略原子需求到唯一实施 Issue 的逐项矩阵仍需继续维护；ADR、跨域 API/事件契约、各产品中心验收细项仍需证据。
- Issue inventory 的“99 个全部读取”与“需求原子项全部映射”是两项不同 DoD，不能互相替代。
- PR merge、代码存在、测试文件存在、CI success、生产/现场验收必须分别记录。未观察到的 CI 记为 pending/unknown；失败或取消明确保留。
- 真机/生产环境验证不在本次任务范围；不得以本次文档归档宣称已完成。

## Validation performed for this archive

- Static Markdown structure and link-target review is required in CI.
- Source documents are carried forward without rewriting their historical findings; this index resolves conflicting snapshots by date and explains canonical ownership.
- No product code, Issue state, or existing PR merge state is changed by this archive.
