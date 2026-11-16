#!/usr/bin/env python3
"""多维表格凭据按环境取：dev 没有独立表就拒绝，绝不写正式表。"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "agency" / "config.json").read_text(encoding="utf-8"))


def env_name():
    env = os.environ.get("AGENCY_ENV", "").strip().lower()
    if env not in ("dev", "prod"):
        sys.exit("拒绝执行：请显式设置 AGENCY_ENV=dev 或 AGENCY_ENV=prod。")
    return env


def base_token(required=True):
    env = env_name()
    feishu = CONFIG.get("feishu", {})
    tokens = feishu.get("base_tokens") or {}
    token = (os.environ.get("FEISHU_BASE_TOKEN")
             or tokens.get(env) or (feishu.get("base_token") if env == "prod" else "")
             or "").strip()
    if not token and required:
        sys.exit("拒绝执行：%s 未配置独立多维表格 token，不能写正式表。" % env)
    prod_token = (tokens.get("prod") or feishu.get("base_token") or "").strip()
    if env == "dev" and prod_token and token == prod_token:
        sys.exit("拒绝执行：dev 的 token 与正式表相同，禁止写正式表。")
    return token


def require_token():
    return base_token(required=True)
