#!/usr/bin/env python3
"""feishu_bot.py — 飞书机器人（应用身份）最小客户端

定位：Agent 的嘴巴和耳朵。只负责任务增删改查与消息推送，
不碰文档与 wiki（文档走用户身份，见 ADR-005）。

纯标准库，无第三方依赖。

用法：
  python3 feishu_bot.py check                          验证凭据与权限
  python3 feishu_bot.py msg "文本"                      给自己发一条消息
  python3 feishu_bot.py task create --title "..." --due 2026-11-20T18:00+08:00
  python3 feishu_bot.py task get <task_guid>
  python3 feishu_bot.py task list
  python3 feishu_bot.py task complete <task_guid>
"""

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# 默认国内站 + 北京时间；可用 FEISHU_BASE_URL / FEISHU_TZ_OFFSET 覆盖（如 Lark 国际站）。
DEFAULT_BASE = "https://open.feishu.cn/open-apis"
DEFAULT_TZ_OFFSET = 8

# 凭据失效类错误码：换 token 后重试一次
TOKEN_INVALID = {99991661, 99991663, 99991668}
RATE_LIMIT = 99991400


def base_url():
    """飞书 OpenAPI 域名；默认值与历史硬编码完全一致。"""
    load_env()
    return (os.environ.get("FEISHU_BASE_URL") or DEFAULT_BASE).rstrip("/")


def local_tz():
    """业务时区；FEISHU_TZ_OFFSET 为小时偏移，默认 +8。"""
    load_env()
    raw = os.environ.get("FEISHU_TZ_OFFSET")
    try:
        hours = float(raw) if raw not in (None, "") else DEFAULT_TZ_OFFSET
    except ValueError:
        hours = DEFAULT_TZ_OFFSET
    return dt.timezone(dt.timedelta(hours=hours))


# 兼容旧引用；实际调用统一走 base_url() / local_tz()，运行时读环境变量。
BASE = DEFAULT_BASE
LOCAL_TZ = dt.timezone(dt.timedelta(hours=DEFAULT_TZ_OFFSET))


def load_env():
    """按 AGENCY_ENV 读 .env / .env.prod / .env.dev；已存在的环境变量优先。

    单独跑 `python3 feishu_bot.py check` 时也要能拿到正式凭据，
    因此显式带 AGENCY_ENV 时追加对应环境文件；未带时只读 .env。
    """
    env = (os.environ.get("AGENCY_ENV") or "").strip().lower()
    names = [".env"]
    if env:
        names.append(".env.prod" if env == "prod" else ".env." + env)
    for name in names:
        path = ROOT / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if not value:
                continue
            # dev 的飞书凭据只能来自 .env.dev，绝不继承 prod 应用
            if env and env != "prod" and name == ".env" and key.startswith("FEISHU_"):
                continue
            os.environ.setdefault(key, value)


class FeishuError(Exception):
    def __init__(self, code, msg, path="", http_status=None):
        self.code = code
        self.msg = msg
        self.path = path
        self.http_status = http_status
        super().__init__(f"[{code}] {msg} ({path})")

    def advice(self):
        if self.code in TOKEN_INVALID:
            return "凭据失效：检查 FEISHU_APP_ID / FEISHU_APP_SECRET，或应用未发布上线。"
        if self.code == 99992361:
            return ("open_id 跨应用：open_id 按应用隔离，该值属于别的应用。\n"
                    "      两个解法：1) 在飞书里给机器人发一条消息，再把 FEISHU_CHAT_ID 填进 .env；\n"
                    "                2) 用本应用查到的 open_id。")
        if self.code == 403 or self.code == 99991672:
            return "权限不足：应用未开通对应权限，或开了但没发布新版本（权限发布后才生效）。"
        if self.code == RATE_LIMIT or self.http_status == 429:
            return "被限流：稍后重试，或降低调用频率。"
        return ""


class AuthExpired(FeishuError):
    pass


def to_timestamp(value):
    """接受 'YYYY-MM-DD' 或 ISO8601，返回秒级时间戳字符串。

    只有日期时按当天 23:59:59（UTC+8），避免"当天到期"被算成零点。
    """
    if isinstance(value, (int, float)):
        return str(int(value))
    s = str(value).strip()
    if len(s) == 10:
        d = dt.date.fromisoformat(s)
        moment = dt.datetime.combine(d, dt.time(23, 59, 59), local_tz())
    else:
        moment = dt.datetime.fromisoformat(s)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=local_tz())
    return str(int(moment.timestamp()))


def from_timestamp(ts):
    if not ts:
        return ""
    return dt.datetime.fromtimestamp(int(ts), local_tz()).strftime("%Y-%m-%d %H:%M")


class Bot:
    """应用身份客户端。token 带内存缓存，失效自动换一次。"""

    def __init__(self, app_id=None, app_secret=None, user_open_id=None):
        load_env()
        self.app_id = app_id or os.environ.get("FEISHU_APP_ID", "")
        self.app_secret = app_secret or os.environ.get("FEISHU_APP_SECRET", "")
        self.user_open_id = user_open_id or os.environ.get("FEISHU_USER_OPEN_ID", "")
        self._token = ""
        self._token_exp = 0.0

    # ---------- 底层 ----------

    def _token_value(self, force=False):
        if not force and self._token and time.time() < self._token_exp:
            return self._token
        if not self.app_id or not self.app_secret:
            raise AuthExpired(0, "缺少 FEISHU_APP_ID 或 FEISHU_APP_SECRET", "/.env")
        payload = {"app_id": self.app_id, "app_secret": self.app_secret}
        data = self._raw("POST", "/auth/v3/tenant_access_token/internal", payload)
        if data.get("code") != 0:
            raise AuthExpired(data.get("code"), data.get("msg", ""), "/auth/v3/tenant_access_token/internal")
        self._token = data["tenant_access_token"]
        # 提前 5 分钟过期，避免边界
        self._token_exp = time.time() + max(int(data.get("expire", 7200)) - 300, 60)
        return self._token

    def _raw(self, method, path, body=None, query=None):
        url = base_url() + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json; charset=utf-8")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                raise FeishuError(e.code, raw[:200], path, e.code) from e
        except urllib.error.URLError as e:
            raise FeishuError(0, f"网络不可达：{e.reason}", path) from e

    def call(self, method, path, body=None, query=None, retries=3):
        """带 token 的请求。401 类错误换 token 重试；429 退避重试。"""
        attempt = 0
        refreshed = False
        while True:
            attempt += 1
            token = self._token_value(force=refreshed)
            url = base_url() + path
            if query:
                url += "?" + urllib.parse.urlencode(query)
            data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
            req = urllib.request.Request(url, data=data, method=method)
            req.add_header("Content-Type", "application/json; charset=utf-8")
            req.add_header("Authorization", "Bearer " + token)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                raw = e.read().decode("utf-8", "replace")
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    payload = {"code": e.code, "msg": raw[:200], "_http": e.code}
                payload.setdefault("_http", e.code)
            except urllib.error.URLError as e:
                raise FeishuError(0, f"网络不可达：{e.reason}", path) from e

            code = payload.get("code", 0)
            if code == 0:
                return payload.get("data", {})

            if code in TOKEN_INVALID and not refreshed:
                refreshed = True
                continue

            http = payload.get("_http")
            if (code == RATE_LIMIT or http == 429) and attempt < retries:
                time.sleep(2 ** attempt)
                continue

            err = FeishuError(code, payload.get("msg", ""), path, http)
            hint = err.advice()
            if hint:
                err.args = (f"{err}\n{hint}",)
            raise err

    # ---------- 能力 ----------

    def whoami(self):
        """返回机器人自身信息，同时验证凭据有效。"""
        data = self.call("GET", "/bot/v3/info")
        return data.get("bot", data)

    def chat_list(self, page_size=50):
        """机器人所在的会话。给机器人发过消息后，这里会出现单聊。"""
        data = self.call("GET", "/im/v1/chats", query={"page_size": page_size})
        return data.get("items", [])

    def resolve_open_id(self, email=None, mobile=None):
        """按邮箱或手机号查本应用下的 open_id（open_id 按应用隔离，必须现查）。"""
        body = {}
        if email:
            body["emails"] = [email]
        if mobile:
            body["mobiles"] = [mobile]
        if not body:
            raise FeishuError(0, "需要 --email 或 --mobile", "/contact/v3/users/batch_get_id")
        data = self.call("POST", "/contact/v3/users/batch_get_id", body,
                         query={"user_id_type": "open_id"})
        for u in data.get("user_list", []):
            if u.get("user_id"):
                return u["user_id"]
        return ""

    def _resolve(self, receive_id=None, id_type=None):
        """收件人解析：显式入参 > FEISHU_CHAT_ID > FEISHU_USER_OPEN_ID。"""
        if receive_id:
            return receive_id, id_type or "open_id"
        chat_id = os.environ.get("FEISHU_CHAT_ID", "")
        if chat_id:
            return chat_id, "chat_id"
        if self.user_open_id:
            return self.user_open_id, "open_id"
        raise FeishuError(0, "没有收件人：填 FEISHU_CHAT_ID 或 FEISHU_USER_OPEN_ID", "/im/v1/messages")

    def send_text(self, text, receive_id=None, id_type=None):
        rid, idt = self._resolve(receive_id, id_type)
        return self.call(
            "POST",
            "/im/v1/messages",
            {"receive_id": rid, "msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)},
            query={"receive_id_type": idt},
        )

    def send_card(self, card, receive_id=None, id_type=None):
        rid, idt = self._resolve(receive_id, id_type)
        return self.call(
            "POST",
            "/im/v1/messages",
            {"receive_id": rid, "msg_type": "interactive", "content": json.dumps(card, ensure_ascii=False)},
            query={"receive_id_type": idt},
        )

    def task_create(self, title, due=None, start=None, owner=None, description=""):
        body = {"summary": title}
        if description:
            body["description"] = description
        if start:
            body["start"] = {"timestamp": to_timestamp(start), "is_all_day": False}
        if due:
            body["due"] = {"timestamp": to_timestamp(due), "is_all_day": False}
        if owner:
            body["members"] = [{"id": owner, "type": "user", "role": "assignee"}]
        data = self.call("POST", "/task/v2/tasks", body, query={"user_id_type": "open_id"})
        return data.get("task", data)

    def task_get(self, guid):
        data = self.call("GET", f"/task/v2/tasks/{guid}", query={"user_id_type": "open_id"})
        return data.get("task", data)

    def task_update(self, guid, fields):
        data = self.call(
            "PATCH",
            f"/task/v2/tasks/{guid}",
            {"task": fields, "update_fields": list(fields.keys())},
            query={"user_id_type": "open_id"},
        )
        return data.get("task", data)

    def task_complete(self, guid):
        ts = str(int(time.time()))
        return self.task_update(guid, {"completed_at": ts})

    def task_list(self, page_size=50, completed=False):
        """注意：飞书任务列表接口通常要求 user_access_token。

        应用身份调用可能返回权限错误，此时明确报错，不静默返回空。
        """
        data = self.call(
            "GET",
            "/task/v2/tasks",
            query={"page_size": page_size, "completed": str(completed).lower(), "user_id_type": "open_id"},
        )
        return data.get("items", [])


# ---------- CLI ----------


def cmd_check(_):
    load_env()
    missing = [k for k in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_USER_OPEN_ID") if not os.environ.get(k)]
    if missing:
        print("[缺失] .env 缺少：" + "、".join(missing))
        print("拿到 App Secret 后写入 .env（chmod 600），再跑一次 check。")
        return 1
    print("[OK ] .env 三个变量齐全")
    bot = Bot()
    try:
        info = bot.whoami()
    except FeishuError as e:
        print("[失败]", e)
        return 1
    print(f"[OK ] 凭据有效，机器人：{info.get('app_name') or info.get('name') or '(未命名)'}")
    try:
        bot.send_text("底座自检：机器人通道已打通。")
        print("[OK ] 消息发送成功")
    except FeishuError as e:
        print("[失败] 消息发送：", e)
        if e.code == 99992361:
            chats = bot.chat_list()
            p2p = [c for c in chats if c.get("chat_mode") == "p2p"]
            if p2p:
                chat_id = p2p[0]["chat_id"]
                bot.send_text("底座自检：机器人通道已打通。", receive_id=chat_id, id_type="chat_id")
                print("[OK ] 改用单聊会话发送成功，把这一行写进 .env：")
                print(f"      FEISHU_CHAT_ID={chat_id}")
            else:
                print("      机器人还没有任何会话。请在飞书里搜索「FDE管家」并发送任意一句话。")
        return 1
    try:
        t = bot.task_create("底座自检任务（可删除）",
                            due=dt.datetime.now(local_tz()) + dt.timedelta(days=1))
        print(f"[OK ] 任务创建成功：{t.get('guid')}")
        bot.call("DELETE", f"/task/v2/tasks/{t.get('guid')}")
        print("[OK ] 自检任务已清理")
    except FeishuError as e:
        print("[失败] 任务创建：", e)
        print("      多半是 task:task:write 未开通或版本未发布。")
        return 1
    print("自检结果：全绿")
    return 0


def cmd_id(args):
    bot = Bot()
    oid = bot.resolve_open_id(email=args.email, mobile=args.mobile)
    if not oid:
        print("没查到：该邮箱/手机号不在本租户通讯录，或未验证。")
        return 1
    print("open_id =", oid)
    print("写进 .env：FEISHU_USER_OPEN_ID=" + oid)
    return 0


def cmd_msg(args):
    data = Bot().send_text(args.text)
    print("已发送，message_id:", data.get("message_id"))


def cmd_task(args):
    bot = Bot()
    if args.action == "create":
        t = bot.task_create(args.title, due=args.due, start=args.start,
                            owner=args.owner or bot.user_open_id, description=args.desc)
        print(f"已建任务 {t.get('guid')}｜{t.get('summary')}｜截止 {from_timestamp((t.get('due') or {}).get('timestamp'))}")
    elif args.action == "get":
        t = bot.task_get(args.guid)
        print(f"{t.get('guid')}｜{t.get('summary')}｜完成 {from_timestamp(t.get('completed_at'))}｜截止 {from_timestamp((t.get('due') or {}).get('timestamp'))}")
    elif args.action == "list":
        items = bot.task_list()
        if not items:
            print("无任务")
        for t in items:
            print(f"{t.get('guid')}｜{t.get('summary')}｜完成 {from_timestamp(t.get('completed_at'))}")
    elif args.action == "complete":
        bot.task_complete(args.guid)
        print("已标记完成：", args.guid)


def main():
    p = argparse.ArgumentParser(description="飞书机器人最小客户端")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="验证凭据与权限")

    pm = sub.add_parser("msg", help="发文本消息")
    pm.add_argument("text")

    pi = sub.add_parser("id", help="按邮箱/手机号查本应用 open_id")
    pi.add_argument("--email", default="")
    pi.add_argument("--mobile", default="")

    pt = sub.add_parser("task", help="任务操作")
    pt.add_argument("action", choices=["create", "get", "list", "complete"])
    pt.add_argument("guid", nargs="?", default="")
    pt.add_argument("--title", default="")
    pt.add_argument("--due", default=None)
    pt.add_argument("--start", default=None)
    pt.add_argument("--owner", default=None)
    pt.add_argument("--desc", default="")

    args = p.parse_args()
    try:
        if args.cmd == "check":
            sys.exit(cmd_check(args))
        if args.cmd == "id":
            sys.exit(cmd_id(args))
        if args.cmd == "msg":
            cmd_msg(args)
        elif args.cmd == "task":
            cmd_task(args)
    except FeishuError as e:
        print("错误：", e)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
