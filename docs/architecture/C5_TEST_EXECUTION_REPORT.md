# C5 WP-E：真实测试执行记录

## 1. 执行目标与基线

- Issue：[#848](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/848)
- 任务：C5 WP-E，前端生产构建、核心后端测试矩阵、执行结果、跳过原因与覆盖率。
- 代码基线：`audit/issue-848-ai-native-gap-assessment`（以 PR base SHA 为准）。
- Node 版本：仓库 `.nvmrc` 声明 `20.19.0`。
- Python 矩阵：3.11、3.12。
- 前端构建命令：`cd frontend && npm install --no-package-lock && npm run build`。
- 后端核心测试：CI 中执行身份/租户授权专项 unittest，以及 `python -m unittest discover -s tests -t . -q` 全量测试。
- 覆盖率：`coverage run --branch --source=agent_platform -m unittest discover -s tests -t . -q`，输出终端报告及 `coverage.xml` artifact。

## 2. 执行环境限制

本会话执行环境没有仓库工作副本，尝试从 GitHub 克隆时因运行容器 DNS 无法解析 `github.com` 而失败。因此，**没有声称本地 npm build 或本地 Python 测试已执行**。为获得真实可复现结果，本 PR 新增专用 GitHub Actions 工作流 `.github/workflows/c5-test-execution.yml`，由 PR 自动执行上述命令并保存覆盖率产物。该工作流完成前，测试结果和覆盖率均标记为待 CI 实际回填。

## 3. 测试矩阵

| 类别 | 命令/矩阵 | 结果 | 备注 |
|---|---|---|---|
| 前端依赖安装 | `npm install --no-package-lock` | 待 CI | Node 20.19.0；不生成/提交 package-lock |
| 前端生产构建 | `npm run build` | 待 CI | 执行 TypeScript project build 与 Vite production build |
| 身份与租户专项 | `python -m unittest tests.test_identity ... tests.test_issue_755_c_dual_mode_integration -v` | 待 CI | Python 3.11 / 3.12 |
| 后端全量 unittest | `python -m unittest discover -s tests -t . -q` | 待 CI | Python 3.11 / 3.12 |
| 分支覆盖率 | `coverage run --branch --source=agent_platform ...` | 待 CI | 生成 coverage summary 与 XML artifact |
| Lint / mypy | 未纳入本 WP 独立验收命令 | 跳过 | 当前任务聚焦真实测试与构建；仓库主 CI 另有 ruff/mypy 门禁 |
| 浏览器 E2E / Playwright | 未在本工作流执行 | 跳过 | 本次指定重点为 npm build 与核心后端测试矩阵；浏览器 E2E 需要浏览器安装和独立运行时间 |
| 真机 / 真实服务器 / 外部业务系统 | 未执行 | 按调度要求跳过 | 不申请真实服务器或外部资源 |
| 外部模型供应商联调 | 未执行 | 跳过 | 避免外部凭据和计费依赖 |

## 4. 结果与覆盖率回填规则

- 当前文档提交时：CI 尚未完成，所有构建/测试结果均为 **待 CI**，不能填写虚构通过数或覆盖率百分比。
- GitHub Actions 工作流：`.github/workflows/c5-test-execution.yml`。
- Backend coverage artifacts：`c5-backend-coverage-py3.11`、`c5-backend-coverage-py3.12`，在工作流完成后提供真实 `coverage.xml`。
- PR 评论和最终交付状态将以工作流实际结论更新；失败时保留失败 job、错误摘要与跳过原因，不将失败改写为通过。

## 5. 已知限制

1. 本轮没有本地运行结果；真实命令由 GitHub Actions runner 执行。
2. 测试覆盖率只针对 `agent_platform` Python 源码，并以 coverage 实际报告为准；不代表前端覆盖率或端到端生产覆盖率。
3. 全量 unittest 可能受仓库既有测试失败、可选依赖或环境差异影响；失败需按日志区分代码失败、依赖失败和外部服务依赖。
4. 未执行真实服务器部署、真机验证和外部业务系统联调。

