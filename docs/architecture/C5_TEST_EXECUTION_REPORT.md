# C5 WP-E：真实测试执行记录

## 1. 执行目标与基线

- Issue：[#848](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/848)
- 任务：C5 WP-E，前端生产构建、核心后端测试矩阵、执行结果、跳过原因与覆盖率。
- PR：[#868](https://github.com/dujiu1024-dotcom/Agent-pingtai/pull/868)
- 代码基线：`audit/issue-848-ai-native-gap-assessment`（PR base SHA：`442863ab00cf08617f4e21d53d2a373cd5054bb5`）。
- 首轮执行提交：`86daffaae56da1489502ead272ab1669ddcf1553`。
- Node 版本：仓库 `.nvmrc` 声明 `20.19.0`。
- Python 矩阵：3.11、3.12。
- 前端构建命令：`cd frontend && npm install --no-package-lock && npm run build`。
- 后端核心测试：Python 3.11/3.12 均执行身份/租户授权专项 unittest；全量 unittest + 分支覆盖率仅在 Python 3.11 执行一次，避免两个版本重复消耗一小时 runner。
- 覆盖率：Python 3.11 执行 `coverage run --branch --source=agent_platform -m unittest discover -s tests -t . -v`，逐测试输出进度并生成终端报告及 `coverage.xml` artifact。

## 2. 执行环境与真实结果

GitHub Actions 首轮执行记录：

- Workflow：`C5 real test execution matrix`
- Run：[#1](https://github.com/dujiu1024-dotcom/Agent-pingtai/actions/runs/44902799369)
- Run ID：`44902799369`
- 结论：**failure**，不是 pending，也不是通过。

### 2.1 后端专项测试

Python 3.11 和 3.12 均执行了 79 个专项测试，结果均为：

- 失败：1
- 跳过：2
- 失败用例：`tests.test_platform_api_app.PlatformApiCompositionTests.test_file_assets_are_isolated_by_tenant_in_authenticated_api`
- 失败位置：`tests/test_platform_api_app.py:214`
- 实际断言：同租户请求返回 HTTP 405，测试预期 HTTP 200。
- 错误：`AssertionError: 405 != 200`

这属于真实 API 路由/方法行为与测试预期不一致，当前不能判定租户文件资产链路通过。

### 2.2 全量测试与覆盖率

首轮工作流中，专项测试失败导致后续全量 unittest + coverage 步骤被 GitHub Actions 跳过。因此：

- 全量 unittest：未执行（skipped），不是通过。
- 分支覆盖率：未生成。
- `.coverage` 与 `coverage.xml`：未产生，首轮 artifact 上传步骤提示未找到文件。
- 覆盖率百分比：无有效数据，不填写虚构数值。

为避免专项失败阻断全量测试，本 PR 已调整工作流：专项测试和全量测试在同一 shell 步骤中分别执行、分别记录退出码；即使专项失败也继续执行全量测试和覆盖率报告，最后汇总失败状态。

### 2.3 前端构建

Node `20.19.0`、npm `10.8.2`，依赖安装成功；`npm run build` 在 TypeScript 检查阶段失败，发现 4 项错误：

| 文件 | 错误 |
|---|---|
| `src/components/crud/RegistryCatalogPage.tsx` | TS6133：`RegistryNameRow` 声明但未使用 |
| `src/features/agents/AgentDefinitionPage.tsx` | TS6133：`resolveNames` 导入但未使用 |
| `src/features/tasks/TaskCommandCenter.tsx:721` | TS2345：`string \| null \| undefined` 不能赋给 `string \| undefined` |
| `src/lib/i18n/labels.ts:136` | TS1117：对象字面量存在重复属性 |

以上是当前构建基线的实际失败，不属于本 WP 自动修复范围；本任务记录并报告，不擅自修改业务页面。

依赖安装另报告 16 项 npm audit 漏洞（8 moderate、6 high、2 critical）；本次构建工作流不执行依赖升级。

### 2.4 第二轮执行（Run `44908490647`）\n\n- Python 3.11 与 3.12 的专项测试均执行 79 项：失败 1 项、跳过 2 项；失败仍为上述文件资产租户隔离用例（HTTP 405，预期 200）。\n- 两个版本随后进入全量测试，但均运行至 60 分钟 job timeout 被 GitHub Actions 取消。\n- 取消时未生成 `.coverage` 或 `coverage.xml`；artifact 列表为空，不能报告覆盖率数值。\n- 前端 `npm run build` 本轮通过。\n- 全量测试在 `-q` 模式下没有逐用例进度，因此本轮无法从日志确认具体耗时测试。\n\n## 3. 修正后的测试执行策略

工作流文件：`.github/workflows/c5-test-execution.yml`

- Python 3.11 / 3.12 均执行专项测试；全量测试与覆盖率只在 Python 3.11 执行一次，避免重复运行相同的长耗时套件。\n- 全量 unittest 改为 `-v`，逐用例输出进度，以便下次超时能定位最后执行的测试。
- 专项测试失败后仍继续执行全量 unittest。
- 全量测试结束后生成 coverage summary 和 `coverage.xml`；如果 60 分钟内未完成，按真实超时失败记录，不填报覆盖率。
- 上传步骤使用 `if: always()`，找不到覆盖率文件时发出 warning，不伪装成成功产物。
- 最终根据专项测试、全量测试、覆盖率报告和 XML 生成状态汇总退出码；任一失败则 job 失败。
- 前端构建继续独立执行，保留真实 TypeScript 错误日志。

## 4. 跳过项与边界

| 类别 | 结果 | 备注 |
|---|---|---|
| Lint / mypy | 本 WP 未执行 | 本任务聚焦指定构建与测试矩阵 |
| 浏览器 E2E / Playwright | 未执行 | 本轮指定重点为 npm build 与核心后端测试矩阵 |
| 真机 / 真实服务器 / 外部业务系统 | 按调度要求跳过 | 不申请真实服务器或外部资源 |
| 外部模型供应商联调 | 未执行 | 避免外部凭据和计费依赖 |

## 5. 结论与后续验收

- 首轮真实 CI 已证明当前分支存在后端 API 测试失败和前端 TypeScript 构建失败。
- 首轮覆盖率缺失的直接原因是专项测试失败后，全量测试步骤被跳过。
- 第二轮 run `44908490647` 已超时，未生成覆盖率；工作流现已调整为 Python 3.11 单次全量覆盖率 + 逐用例日志，Python 3.12 保留专项兼容验证，等待新一轮 CI。
- 在第二轮 CI 完成前，最终状态仍为失败/待复验；不得填写通过率、覆盖率百分比或声称交付验收通过。
- 本 PR 不合并；业务代码中的 405 与 TypeScript 问题应作为独立缺陷修复，不在 C5 WP-E 的测试执行记录任务中越界修改。
