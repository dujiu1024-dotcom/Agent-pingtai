#!/usr/bin/env python3
"""alerts.py — 本地告警（不依赖飞书）

任何服务失败都必须留下可见记录：
  1. 追加 agency/state/alerts.log（或 AGENCY_DB 同目录）；
  2. macOS 通知中心弹一条（osascript，失败不影响主流程）。
"""

import datetime as dt
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_DB = os.environ.get("AGENCY_DB")
LOG = (Path(_DB).parent if _DB else ROOT / "agency" / "state") / "alerts.log"


def local_alert(kind, text, notify=True):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "kind": kind, "text": text}
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    if not notify:
        return
    try:
        title = json.dumps("FDE " + kind, ensure_ascii=False)
        body = json.dumps(text[:180].replace("\n", " "), ensure_ascii=False)
        subprocess.run(
            ["osascript", "-e",
             f"display notification {body} with title {title}"],
            capture_output=True, timeout=5)
    except Exception:  # noqa: BLE001 — 通知失败不能影响主流程
        pass
