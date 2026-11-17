# 龍奕FDE工作室 · Agent 运行底座

单文件、纯 Python 标准库、状态全部落盘，可整包迁移。
唯一事实源：`agency/state/agency.db`（SQLite）；飞书只做映射与提醒。

---

## 三步安装

### 第 1 步：装环境

需要 Python 3.8+。主程序（agency.py / doctor）**不需要 pip 安装任何第三方包**；
仅飞书监听 `listener.py` 额外依赖 `lark-oapi`，按 `requirements-listener.txt` 安装：
`pip install -r requirements-listener.txt`。

```bash
python3 --version          # 确认 ≥ 3.8
cd /path/to/FDE工程
python3 agency.py init     # 建库、补表、生成 agency/config.json
```

`init` 可重复执行：已有表不会被覆盖，只补缺失的迁移版本。

### 第 2 步：填密钥

复制 `.env.example` 为 `.env`（没有该文件时直接新建 `.env`），填模型服务的地址与密钥：

```bash
OPENAI_API_KEY=你的密钥
OPENAI_BASE_URL=https://token.sensenova.cn/v1
OPENAI_MODEL=deepseek-v4-flash
```

- `.env` 只放在本地，权限建议 `chmod 600 .env`，不提交、不导出。
- 没有密钥也能用：`doctor`、`task`、`gate`、`wbs`、`check`、`brief`、`vector`（hash 降级）均可离线运行，只有 `run` 需要密钥。

### 第 3 步：自检

```bash
python3 agency.py doctor
```

全部显示 `[OK ]` 即安装成功，末行应为 `自检结果: 通过`。
自检包含路径、角色、闸门、数据库表、单元测试、LLM 密钥、公司宪法 7 项；任一失败退出码为 1。

---

## 常用命令

| 命令 | 作用 |
|---|---|
| `python3 agency.py doctor` | 自检（含单元测试） |
| `python3 agency.py task add --title "..." --project OPC-002 --owner pm --start 2026-11-12 --due 2026-11-13 --done-when "..." --kind code` | 建任务；`--kind code` 需闸门已确认 |
| `python3 agency.py task list` | 看任务与状态（未开始／明日开始／进行中／临期／逾期） |
| `python3 agency.py task close T-0011 --evidence "报告路径"` | 关任务；无证据会被拒绝 |
| `python3 agency.py check` | 检查临期与逾期 |
| `python3 agency.py brief` | 一页态势 |
| `python3 agency.py gate confirm OPC-002 --by 龍奕 --scope "范围"` | 开闸门 |
| `python3 agency.py gate revoke OPC-002` | 撤销闸门（范围变更时先撤销再确认） |
| `python3 agency.py wbs --project OPC-002` | 导出 WBS CSV（只读） |
| `python3 agency.py run planner --task T-0011` | 调用某个 Agent（需密钥） |
| `python3 agency.py export dist.tar.gz` | 交付包：代码/模板/配置，不含 state 数据（自动排除 .env、state 内容、outputs、WAL） |
| `python3 agency.py export backup.tar.gz --with-state` | 迁移包：额外包含 `agency.db` 与事实数据 |
| `python3 agency.py import backup.tar.gz --into /新目录` | 在另一目录恢复（非空目录需 `--force`） |

---

## 本地控制台

不想敲命令时，起一个只绑定本机的操作台：

```bash
python3 console.py              # http://127.0.0.1:8787
python3 console.py --port 9000  # 换端口
```

- 四个视图：态势、任务、里程碑、Agent；支持搜索、筛选、新建任务、关任务（仍须证据）、改期与改负责人。
- 派活：Agent 视图点「派活 →」，选绑定任务、填本次指令，即调用
  `python3 agency.py run <role> --task <id> --note "<指令>"`；页面回显执行输出、耗时、token 与产出路径，
  下方「最近调度」列出历史运行。产出落在 `agency/outputs/`，消耗计入每日 token 上限。
- 只绑定 `127.0.0.1`，不对外网开放；读写同一张台账 `agency/state/agency.db`。
- 纯标准库 `http.server`，无第三方依赖；`Ctrl+C` 停止。
- 属于自用工具（P1），不计入 P0 的 90 小时交付工时。

---

## 单元测试

```bash
python3 -m unittest discover -s tests -t . -v
```

- `tests/test_state.py`：任务状态判定全覆盖（已关闭／要素不全／未开始／明日开始／进行中／临期／逾期）
- `tests/test_gate.py`：闸门三态（未确认／已确认／已撤销）与 `code` 任务拦截；使用临时数据库，不影响正式数据

---

## 目录结构

```
agency.py            运行底座（CLI 全部命令）
console.py           本地控制台服务（127.0.0.1，标准库）
console.html         控制台页面（态势/任务/里程碑/Agent）
db.py                表结构与迁移，枚举唯一定义处
memory.py            工作记忆六槽位
vectors.py           嵌入与检索（Ollama bge-m3 / hash-tfidf 降级）
agents/<角色>/ROLE.md 9 个 Agent 角色定义
company/公司宪法.md   最高优先级规则
clients/<项目>/       客户项目文档
templates/           诊断、报价、报告等可复用模板
tests/               单元测试
agency/state/agency.db 唯一事实源
```

---

## 导出与迁移

两种模式，按用途选：

| 用途 | 命令 | 包内内容 |
|---|---|---|
| 对外交付/分享 | `python3 agency.py export dist.tar.gz` | 代码、模板、配置、测试；`agency/state` 只有空目录，**不含数据库与客户资料** |
| 整包迁移 | `python3 agency.py export backup.tar.gz --with-state` | 在上者基础上增加 `agency.db` 与事实数据 |

交付包在干净目录解压后初始化空库并自检：

```bash
tar -xzf dist.tar.gz -C /新目录 && cd /新目录
python3 agency.py init && python3 agency.py doctor
```

迁移到另一台机器：

1. 旧机器：`python3 agency.py export backup.tar.gz --with-state`
2. 新机器：`python3 agency.py import backup.tar.gz --into /新目录`
3. 重新填 `.env`（密钥不随包导出），再跑 `python3 agency.py doctor`

## 出问题时

- `doctor` 报缺表：`python3 agency.py init`
- 报「闸门未通过」：先 `gate confirm`，或确认该项目确实已签确认单
- 报「没有证据不能关闭任务」：`--evidence` 填产出物路径或命令输出摘要
- 测试失败：单跑 `python3 -m unittest tests.test_gate -v` 看具体用例


---

## 智能体平台改造（新架构）

当前主干保留原 FDE 工作台。新平台在独立目录 `agent_platform/` 中并行建设，按阶段迁移，不影响现有 CLI。

- 架构总设计：[`docs/architecture/智能体平台改造总设计.md`](docs/architecture/智能体平台改造总设计.md)
- 共享领域契约：`agent_platform/contracts.py`
- Hybrid Blackboard 接口：`agent_platform/blackboard/`
- Supervised 审批策略：`agent_platform/approval/`
- LangGraph 与 Harness 适配端口：`agent_platform/runtime/`
- DeepSeek Harness 实验适配器（需隔离环境）：`agent_platform/runtime/deepseek_harness.py`，可选依赖 `pip install -r requirements-harness-dsh.txt`
- Strands Agents 对比适配器（受控模型代理、Gateway-only 工具）：`agent_platform/runtime/strands_agents.py`；官方 MCP Client Provider：`agent_platform/runtime/strands_mcp_provider.py`；可选依赖 `pip install -r requirements-harness-strands.txt`
- Harness 同任务基准测试：`agent_platform/runtime/harness_benchmark.py`，统一比较通过率、延迟、Token 用量与失败类型
- 多专家真实运行时集成测试：覆盖 LangGraph 并行专家、冲突处理、人工审批暂停/恢复
- PostgreSQL + pgvector / Redis Streams 服务级集成测试：`tests/test_platform_postgres_integration.py`、`tests/test_redis_streams_integration.py`，由 `platform-integration` 工作流自动运行
- 模型模板注册与校验：`agent_platform/models/`
- 多专家 LangGraph 流程：`agent_platform/workflows/multi_expert.py`
- PostgreSQL 核心结构：`agent_platform/storage/postgres/`
- Redis Streams 事件传输、Pending Entries 恢复、PostgreSQL 消费回执与幂等消费者：`agent_platform/events/`
- 事件可靠性：PostgreSQL 指数退避（2 秒起、最多 300 秒）、默认 5 次失败上限、Redis 死信 Stream；迁移脚本：`agent_platform/storage/postgres/003_event_retry_and_dlq.sql`

旧 CLI 继续使用标准库。新平台可选依赖单独安装：`pip install -r requirements-platform.txt`。尚未完成的基础设施接入不会影响旧系统运行。


### Gateway-only MCP Profile
- `agent_platform/tools/gateway_mcp.py`：按 Run 创建 MCP Server，只暴露可信白名单内的工具。
- `agent_platform/tools/gateway_mcp_http.py`：按 Run 创建带短期 Bearer Token 校验的 Streamable HTTP ASGI App；Token 校验绑定 actor、资源 URL、过期时间和撤销状态。
- 工具身份、租户、角色、审批 ID、资源解析均来自服务端上下文；参数使用 JSON Schema 校验。
- MCP Server 不直连外部业务系统，所有执行回到 `SupervisedToolGateway`。
- Strands 通过官方 `MCPClient` 连接当前 Run 的 HTTPS MCP endpoint；短期 Bearer Token 由可信控制面按 Run 签发，服务端将 Token 映射到不可变的 tenant/actor/role/run/agent/审批上下文。Provider 使用精确工具过滤，并校验发现集合与 Run 白名单完全一致；缺少 Provider 或 endpoint 时拒绝工具调用。L2 审批 ID 按动作摘要绑定，不按工具名宽泛复用。
- 已通过 MCP SDK 内存传输验证 ClientSession 工具发现和调用；已实现 Strands 官方 MCPClient 的 HTTPS/短期 Token 连接 Provider，并增加连接生命周期、白名单和失败关闭测试。Gateway endpoint 的生产服务化、Token 签发/撤销及真实网络联调仍需完成。


## Legacy v1 archive

The original single-file Python implementation is preserved under `legacy/v1-archive/`. The root-level `agency.py`, `console.py` and `listener.py` paths remain compatibility launchers; `console.html` remains at the root for the gateway's explicit legacy UI fallback. See [legacy archive inventory and rollback notes](legacy/v1-archive/README.md).


## Legacy v1 archive

The archived pre-platform implementation, compatibility boundaries and rollback procedure are documented in [`legacy/v1-archive/README.md`](legacy/v1-archive/README.md). Use `git log --follow -- legacy/v1-archive/<file>` to trace each moved module's history.
