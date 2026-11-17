# Legacy v1 archive

本目录保存旧版 FDE/Agent 单文件应用的原始实现。归档只改变文件路径，不重写业务逻辑；未把这些模块声明为新平台运行时的一部分。

## 归档原因
新平台已在 `agent_platform/` 下形成独立的 API、Runtime、Workflow、存储与 Web 入口。旧版脚本继续留在仓库根目录会造成两个事实入口并存，也容易被误认为仍是新系统的生产入口。因此将旧版顶层脚本与配套控制台/启动脚本集中归档，便于审计、查阅和按需回滚。

## 归档清单（原路径 → 归档路径）
所有文件均保持原始 Git blob（启动脚本为适配归档后的根目录路径做了最小路径修正），Git 可通过 rename detection 追溯。

| 原路径 | 新路径 |
|---|---|
| `agency.py` | `legacy/v1-archive/agency.py` |
| `alerts.py` | `legacy/v1-archive/alerts.py` |
| `boss.py` | `legacy/v1-archive/boss.py` |
| `browser.py` | `legacy/v1-archive/browser.py` |
| `console.py` | `legacy/v1-archive/console.py` |
| `conversation.py` | `legacy/v1-archive/conversation.py` |
| `db.py` | `legacy/v1-archive/db.py` |
| `feishu_bot.py` | `legacy/v1-archive/feishu_bot.py` |
| `feishu_commands.py` | `legacy/v1-archive/feishu_commands.py` |
| `gui.py` | `legacy/v1-archive/gui.py` |
| `intent.py` | `legacy/v1-archive/intent.py` |
| `listener.py` | `legacy/v1-archive/listener.py` |
| `llm_local.py` | `legacy/v1-archive/llm_local.py` |
| `memory.py` | `legacy/v1-archive/memory.py` |
| `policy.py` | `legacy/v1-archive/policy.py` |
| `probe.py` | `legacy/v1-archive/probe.py` |
| `publisher.py` | `legacy/v1-archive/publisher.py` |
| `ratelimit.py` | `legacy/v1-archive/ratelimit.py` |
| `vectors.py` | `legacy/v1-archive/vectors.py` |
| `console.html` | `legacy/v1-archive/console.html` |
| `dashboard.html` | `legacy/v1-archive/dashboard.html` |
| `run-listener.sh` | `legacy/v1-archive/run-listener.sh` |
| `启动服务.command` | `legacy/v1-archive/启动服务.command` |

共 23 个顶层文件，其中 19 个 Python 模块，以及旧控制台页面和启动脚本。核心入口 `agency.py` 原为 7,343 行；其他归档模块包括 `boss.py`、`db.py`、`gui.py`、`listener.py`、`llm_local.py` 等。

## 新旧对照
| 旧版（归档） | 新平台/当前维护位置 |
|---|---|
| CLI/Agent 调度：`agency.py`、`boss.py`、`intent.py`、`conversation.py` | `agent_platform/runtime/`、`agent_platform/workflows/`、`agent_platform/api/` |
| SQLite 与旧状态：`db.py`、`memory.py`、`vectors.py` | `agent_platform/storage/`、`agent_platform/memory/`、`agent_platform/blackboard/` |
| GUI/HTTP 控制台：`gui.py`、`console.py`、`console.html`、`dashboard.html` | `agent_platform/web/`、`agent_platform/api/`、`platform-ui/` |
| 飞书/事件监听：`listener.py`、`feishu_bot.py`、`feishu_commands.py`、`run-listener.sh` | `agent_platform/events/`、连接器模块（按已迁移能力使用） |
| 浏览器、LLM、本地策略与探针：`browser.py`、`llm_local.py`、`policy.py`、`probe.py`、`alerts.py`、`publisher.py`、`ratelimit.py` | `agent_platform/tools/`、`agent_platform/models/`、`agent_platform/observability/` |
| 本地启动入口：`启动服务.command` | 新平台服务入口见 `agent_platform/production.py` 与部署文档 |

职责对照表示迁移方向，不代表每个旧功能都已一一等价迁移。新平台功能以 `agent_platform/` 中实际实现和测试为准。

## 使用与回滚
- 默认部署和开发入口不再从仓库根目录调用旧脚本；归档代码仅供历史查阅和受控恢复。
- 需要恢复归档前的根目录布局时，使用本次归档提交的父提交，或在确认影响后执行 `git revert <archive-commit>`。这会恢复原路径并保留归档提交历史。
- 不建议直接从归档目录启动旧系统：旧版代码使用相对路径定位数据库、配置和产物，迁移后其运行目录语义可能不同。恢复运行前应先恢复原路径，并按旧版 README/部署方式检查本地状态目录。
- 归档不包含任何运行时数据库、密钥、客户数据或外部资源；不执行真机/生产服务器迁移。
