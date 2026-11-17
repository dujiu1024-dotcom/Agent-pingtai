# Legacy v1 archive

This directory preserves the original top-level Python implementation that predates the
platform runtime. Every archived source below is moved byte-for-byte from its former root
path. Git rename detection can associate the archived path with its former path; inspect
history with `git log --follow -- legacy/v1-archive/<file>`.

## Why archived

The active platform implementation lives under `agent_platform/`. Keeping the former
single-file application modules at repository root made the active architecture ambiguous
and encouraged new code to depend on legacy state and service boundaries. This archive
preserves the historical implementation for audit, controlled rollback and compatibility.

## Inventory

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
- `vectors.py` → `legacy/v1-archive/vectors.py`

The original contents are unchanged. Root-level `agency.py`, `console.py` and
`listener.py` remain as compatibility launchers; they add this archive directory to
`sys.path` and execute the archived implementation. This preserves the documented CLI,
local console and `run-listener.sh` entrypoints. The other archived modules are imported
from the archive directory by those launchers.

`console.html`, `run-listener.sh` and `启动服务.command` intentionally remain at their
existing paths. The gateway's legacy UI fallback and the launch scripts depend on these
stable entrypoints; moving them in this task would break the compatibility contract.

## Rollback

To restore the former root layout, move the corresponding archived source back to the
repository root and remove its compatibility launcher in the same commit. Git history
remains available via `git log --follow`; no history rewrite is needed. Do not delete this
archive until the legacy service, launchers and fallback UI have been formally retired.
