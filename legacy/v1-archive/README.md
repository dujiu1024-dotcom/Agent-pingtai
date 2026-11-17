# Legacy v1 archive

This directory preserves the pre-platform, single-file v1 application and launch scripts.

## Why these files are archived

The platform implementation is organized under `agent_platform/`. These former top-level modules and launchers are retained here as historical source. The move reuses original Git blob objects, allowing Git rename detection and preserving file history.

## Archive contents

- `agency.py` → `legacy/v1-archive/agency.py`
- `alerts.py` → `legacy/v1-archive/alerts.py`
- `boss.py` → `legacy/v1-archive/boss.py`
- `browser.py` → `legacy/v1-archive/browser.py`
- `console.py` → `legacy/v1-archive/console.py`
- `conversation.py` → `legacy/v1-archive/conversation.py`
- `db.py` → `legacy/v1-archive/db.py`
- `feishu_bot.py` → `legacy/v1-archive/feishu_bot.py`
- `feishu_commands.py` → `legacy/v1-archive/feishu_commands.py`
- `gui.py` → `legacy/v1-archive/gui.py`
- `intent.py` → `legacy/v1-archive/intent.py`
- `listener.py` → `legacy/v1-archive/listener.py`
- `llm_local.py` → `legacy/v1-archive/llm_local.py`
- `memory.py` → `legacy/v1-archive/memory.py`
- `policy.py` → `legacy/v1-archive/policy.py`
- `probe.py` → `legacy/v1-archive/probe.py`
- `publisher.py` → `legacy/v1-archive/publisher.py`
- `ratelimit.py` → `legacy/v1-archive/ratelimit.py`
- `dashboard.html` → `legacy/v1-archive/dashboard.html`
- `run-listener.sh` → `legacy/v1-archive/run-listener.sh`
- `启动服务.command` → `legacy/v1-archive/启动服务.command`

## New implementation mapping

- Platform API, runtime, task lifecycle and production composition: `agent_platform/`
- Platform web workbench and static assets: `agent_platform/web/`
- Knowledge and RAG: `agent_platform/knowledge/`
- Storage adapters and migrations: `agent_platform/storage/`
- Deployment and operational scripts: `deploy/`, `scripts/`

The archive is not imported by the new platform runtime. Do not add new dependencies on these modules. The frozen top-level `vectors.py` compatibility module is intentionally retained because the knowledge migration policy keeps that legacy entry point available until a separately verified migration.

## Rollback

Revert this archive commit/PR to restore the original top-level paths. Original blob objects are reused, so restoring paths does not require reconstructing file contents. Keep the archive until legacy runtime retirement is explicitly approved.

## Runtime boundary

This is a source-tree archival move only. It does not claim the old runtime remains executable after the move, that the old UI has been cut over, or that production traffic has migrated. Remaining external launcher/import references must be updated or explicitly retained before deployment.
