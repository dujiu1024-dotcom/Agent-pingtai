# AI 原生运行底座 · Agent Platform

面向软件公司的 **AI 原生运行底座**：多租户、可私有部署、工具安全、可灰度迁移。
装上底座 + 加一个行业 Agent = 一个可交付的 AI 产品（如「AI 调酒师」「AI 客服」「AI 法律助手」）。

> **一句话**：一套底座装进客户环境，软件公司贴自己的品牌、加自己的行业 Agent，就是一个企业级就绪的 AI 产品。

---

## 它是什么

一套跑在生产、经过治理的 **Agent 运行平台**，不是 demo 框架。软件公司负责找客户、装平台、加行业 Agent、做交付；底座提供 Agent 运行、工具安全、多租户、审计、配额、可观测等企业级能力。

**产品公式**：AI 原生底座 ＋ 行业 Agent ＝ 客户要的解决方案
- 底座负责「跑得起来、安全、可运维」——通用引擎，一次开发
- Agent 负责「懂这个行业」——差异化产品，属于软件公司，按行业复制

**12+ 能力域，开箱即用**：多租户与身份 / Agent 运行时 / 工作流编排 / 共享认知黑板 / 工具安全网关 / 知识库与检索 / 记忆与上下文 / 评测治理 / 配额与成本 / 可观测 / 事件与可靠性 / 扩展机制。

---

## 快速开始（本地开发）

需要 Python 3.8+。主程序**不需要 pip 安装任何第三方包**；仅飞书监听额外依赖 `lark-oapi`。

```bash
python3 --version          # 确认 ≥ 3.8
cd /path/to/FDE工程
python3 agency.py init     # 建库、补表、生成 agency/config.json
```

填密钥：复制 `.env.example` 为 `.env`，填模型服务地址与密钥：

```bash
OPENAI_API_KEY=你的密钥
OPENAI_BASE_URL=https://token.sensenova.cn/v1
OPENAI_MODEL=deepseek-v4-flash
```

- `.env` 只放本地，权限建议 `chmod 600 .env`，不提交、不导出。
- 没有密钥也能用：`doctor`、`task`、`gate`、`wbs`、`check`、`brief`、`vector`（hash 降级）均可离线运行，只有 `run` 需要密钥。

自检：

```bash
python3 agency.py doctor
```

全部显示 `[OK ]` 即成功，末行应为 `自检结果: 通过`。自检包含路径、角色、闸门、数据库表、单元测试、LLM 密钥、公司宪法 7 项；任一失败退出码为 1。

---

## 常用命令

| 命令 | 作用 |
|---|---|
| `python3 agency.py doctor` | 自检（含单元测试） |
| `python3 agency.py task add --title "..." --project <ID> --owner pm --start 2026-11-12 --due 2026-11-13 --done-when "..." --kind code` | 建任务；`--kind code` 需闸门已确认 |
| `python3 agency.py task list` | 看任务与状态（未开始／明日开始／进行中／临期／逾期） |
| `python3 agency.py task close <ID> --evidence "报告路径"` | 关任务；无证据会被拒绝 |
| `python3 agency.py check` | 检查临期与逾期 |
| `python3 agency.py brief` | 一页态势 |
| `python3 agency.py gate confirm <ID> --by <人> --scope "范围"` | 开闸门 |
| `python3 agency.py gate revoke <ID>` | 撤销闸门（范围变更时先撤销再确认） |
| `python3 agency.py wbs --project <ID>` | 导出 WBS CSV（只读） |
| `python3 agency.py run <role> --task <ID>` | 调用某个 Agent（需密钥） |
| `python3 agency.py export dist.tar.gz` | 交付包：代码/模板/配置，不含 state 数据 |
| `python3 agency.py export backup.tar.gz --with-state` | 迁移包：额外包含数据库与事实数据 |
| `python3 agency.py import backup.tar.gz --into /新目录` | 在另一目录恢复（非空目录需 `--force`） |

---

## 本地控制台

```bash
python3 console.py              # http://127.0.0.1:8787
python3 console.py --port 9000  # 换端口
```

- 四个视图：态势、任务、里程碑、Agent；支持搜索、筛选、新建任务、关任务（仍须证据）、改期与改负责人。
- 派活：Agent 视图点「派活 →」，选绑定任务、填本次指令，即调用 `python3 agency.py run <role> --task <id> --note "<指令>"`。
- 只绑定 `127.0.0.1`，不对外网开放；读写同一张台账 `agency/state/agency.db`。
- 纯标准库 `http.server`，无第三方依赖；`Ctrl+C` 停止。

> 生产部署请使用新平台 `agent_platform/`（见下），支持私有部署 / 专有云 / SaaS 多租户多种形态。

---

## 单元测试

```bash
python3 -m unittest discover -s tests -t . -v
```

---

## 目录结构

```
agency.py            运行底座（CLI 全部命令）
console.py           本地控制台服务（127.0.0.1，标准库）
agent_platform/      AI 原生运行底座（新平台，生产部署用）
  ├─ identity/       多租户、RBAC、服务账号、资源范围
  ├─ runtime/        Agent Runtime（Harness 适配层）
  ├─ blackboard/     共享认知黑板（Hybrid Blackboard）
  ├─ gateway/        工具安全网关（Supervised Tool Gateway）
  ├─ workflows/      工作流编排（LangGraph）
  ├─ extensions/     扩展机制（行业 Agent 打包）
  ├─ deployment/     部署与迁移（expand/contract、备份恢复）
  ├─ switching/      租户灰度切换
  ├─ storage/        PostgreSQL 数据模型与迁移
  ├─ events/         事件可靠性（Outbox、Redis Streams）
  ├─ quotas/         配额与成本
  ├─ observability/  可观测（Langfuse）
  └─ sdk/            版本化 API 的同步 SDK
agents/<角色>/ROLE.md  Agent 角色定义
company/公司宪法.md   最高优先级规则
docs/architecture/    架构契约文档
tests/               单元测试
```

---

## 导出与迁移

| 用途 | 命令 | 包内内容 |
|---|---|---|
| 对外交付/分享 | `python3 agency.py export dist.tar.gz` | 代码、模板、配置、测试；**不含数据库与客户资料** |
| 整包迁移 | `python3 agency.py export backup.tar.gz --with-state` | 在上者基础上增加数据库与事实数据 |

迁移到另一台机器：旧机器 export `--with-state` → 新机器 import → 重新填 `.env`（密钥不随包导出）→ 跑 doctor。

---

## 新平台架构（生产部署用）

当前主干保留轻量 CLI 工作台。生产级 AI 原生底座在 `agent_platform/` 中建设，支持多租户、私有部署、灰度迁移。

- 架构总设计：[`docs/architecture/智能体平台改造总设计.md`](docs/architecture/智能体平台改造总设计.md)
- 共享领域契约：`agent_platform/contracts.py`（仅依赖标准库，可脱离 PG/Redis/LLM 单测）
- 核心能力模块：`identity/`、`runtime/`、`blackboard/`、`gateway/`、`workflows/`、`extensions/`、`deployment/`、`switching/`、`events/`、`quotas/`、`observability/`
- PostgreSQL 核心结构：`agent_platform/storage/postgres/`（103+ 迁移脚本）
- 数据模型迁移编排：expand/contract 八阶段状态机（可暂停、可回滚），见 `deployment/migrations.py`
- 租户灰度切换：`switching/gateway.py`（按租户 allowlist + 流量百分比 HMAC 路由）
- 扩展机制：`extensions/`（声明式 Manifest、生命周期、信任发布者、OCI 沙箱）
- 工具安全网关：`gateway/`（MCP 白名单、风险分级 L0–L3、一次性审批绑定）
- 事件可靠性：PostgreSQL Outbox + Redis Streams，指数退避（2s 起、封顶 300s），默认 5 次失败进死信，消费幂等回执去重

新平台可选依赖单独安装：`pip install -r requirements-platform.txt`。尚未完成的基础设施接入不会影响旧系统运行。

### Gateway-only MCP Profile
- `agent_platform/tools/gateway_mcp.py`：按 Run 创建 MCP Server，只暴露可信白名单内的工具。
- `agent_platform/tools/gateway_mcp_http.py`：按 Run 创建带短期 Bearer Token 校验的 Streamable HTTP ASGI App。
- 工具身份、租户、角色、审批 ID、资源解析均来自服务端上下文；MCP Server 不直连外部业务系统，所有执行回到 `SupervisedToolGateway`。

---

## 出问题时

- `doctor` 报缺表：`python3 agency.py init`
- 报「闸门未通过」：先 `gate confirm`，或确认该项目确实已签确认单
- 报「没有证据不能关闭任务」：`--evidence` 填产出物路径或命令输出摘要

---

## 渠道合作

这套底座面向**软件公司**开放渠道合作：你们有行业客户与 know-how，我们提供跑得起来的 AI 原生底座。装上就是新产品，交付可复制，企业级就绪。详见 PPT 介绍或联系项目方。

---

## Legacy v1 archive

The original single-file Python implementation is preserved under `legacy/v1-archive/`. The root-level `agency.py`, `console.py` and `listener.py` paths remain compatibility launchers. See [legacy archive inventory and rollback notes](legacy/v1-archive/README.md).
