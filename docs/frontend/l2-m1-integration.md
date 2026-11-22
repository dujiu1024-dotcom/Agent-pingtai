# L2-M1 前端集成核验

核验基线：本分支从当前 `main` 建立。任务目标为汇总已交付的前端实现、验证路由集成，并由 `frontend-ci` 对集成分支执行完整前端质量门禁。

## 已在 main 的前端交付（不重复 cherry-pick）

经 GitHub PR 元数据核对，以下前端交付已合入 `main`，其提交已包含在本集成分支起点，因此无需也不应再次 cherry-pick：

- 工程基座与全局交互：#789、#908、#910、#913
- 驾驶舱、任务、协作、成本质量：#917、#919、#928、#930
- Office 与管理后台：#904、#923、#927、#924
- 新旧前端并行构建与认证 E2E：#933
- Round 3 路由/登录/设置接线：#939、#940、#941、#943、#947
- 灰度回滚策略契约：#968

这些 PR 当前均为 merged；本任务不复制其代码或创建第二套组件。

## 排除项

- PR #936（Issue #815）当前 closed 且 merged=false，不属于已合入代码，不把其分支代码静默混入本次集成。若需要该 Dashboard 路由切换，应由其原任务负责人按 Issue #815 单独恢复/验收。
- 本轮没有改动其他未合并的业务功能 PR，也没有关闭/合并任何 PR。

## 本轮集成改动

- `frontend/src/routes/route-config.test.ts` 增加全局路由集成契约测试：所有一级导航和子导航均必须映射到注册路由；全量路由路径不得重复；覆盖主要工作台、Office、Agent/Workflow、Tool/Skill、设置、审计与运维入口。
- 测试只核对真实 route registry，不伪造 API、页面状态或业务数据。

## CI 验证

`frontend-ci` 是本集成分支的最终自动化验收依据，执行 Unit tests、Lint/Prettier、TypeScript、生产构建、并行候选构建、Playwright 核心 E2E 和 Auth/Cutover E2E。未取得当前 PR HEAD 的终态前，不标记为通过。

## 约束

- 不执行 PR merge；本 PR 仅供审查与 CI 验证。
- 真机、生产服务器、外部资源和真实部署切流验证均跳过。
- 调度文本指定 Issue #0，但 GitHub 对仓库 `issues/0` 返回 404；因此无法读取 Issue #0 原始任务清单，也无法在不存在的 Issue #0 评论区回填。本文件和 PR 仅记录可验证的集成事实，不把 Issue #742 或 #267 冒充为 Issue #0。
