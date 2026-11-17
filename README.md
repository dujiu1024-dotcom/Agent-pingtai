# Agent 平台

本仓库当前维护的新平台位于 `agent_platform/`，包括 API、Runtime、Workflow、租户身份、安全、持久化、事件、可观测性与 Web 工作台。

## 开发入口
- 平台架构与模块说明：`docs/architecture/`
- 应用组合入口：`agent_platform/production.py`
- API：`agent_platform/api/`
- Runtime 与 Workflow：`agent_platform/runtime/`、`agent_platform/workflows/`
- Web 工作台：`agent_platform/web/` 与 `platform-ui/`
- 测试：`tests/`

安装与测试命令以 `pyproject.toml`、`requirements-platform.txt` 和 CI workflow 为准。

## 旧版 v1 代码归档
旧版顶层单文件应用已整体迁移至 [`legacy/v1-archive/`](legacy/v1-archive/)，包括旧 CLI、SQLite 数据层、GUI/控制台、监听器、LLM/浏览器辅助模块及其启动脚本。归档原因、新旧职责对照、完整文件清单与回滚方式见 [归档说明](legacy/v1-archive/README.md)。

归档只改变路径（启动脚本为适配归档后的仓库根目录做了最小路径修正），原业务模块的 Git blob 保持不变。旧版入口不再作为仓库根目录的默认运行入口；不要将归档目录中的代码误认为新平台的生产实现。
