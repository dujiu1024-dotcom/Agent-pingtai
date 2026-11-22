"""通用数据连接器实现。

每个 provider 只负责「用凭据 + scope 拉取一页数据」，凭据由平台在调用时
临时解析，不落库。scope 约定见各 provider 文档；未安装可选驱动或凭据缺失
时抛出中文可读错误，交由调度作业与 API 展示。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Mapping
from urllib.parse import quote, urlencode, urlsplit
from uuid import UUID
from xml.etree import ElementTree

import httpx

from agent_platform.control_plane.data_connectors import ConnectorError, ConnectorPage

DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

PROVIDER_LABELS: dict[str, str] = {
    "postgres": "PostgreSQL / 只读 SQL",
    "mysql": "MySQL / 只读 SQL",
    "s3": "S3 / 对象存储",
    "feishu": "飞书云文档 / 多维表格",
    "notion": "Notion 数据库 / 页面",
    "http": "通用 HTTP JSON 接口",
}


def _credential(credential: str) -> dict[str, Any]:
    text = (credential or "").strip()
    if not text:
        raise ConnectorError("连接器凭据为空")
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ConnectorError("连接器凭据 JSON 解析失败") from exc
        if not isinstance(data, dict):
            raise ConnectorError("连接器凭据必须是 JSON 对象")
        return data
    return {"dsn": text}


def _page_size(conf: Mapping[str, Any]) -> int:
    try:
        value = int(conf.get("page_size") or DEFAULT_PAGE_SIZE)
    except (TypeError, ValueError):
        value = DEFAULT_PAGE_SIZE
    return max(1, min(value, MAX_PAGE_SIZE))


def _scope_entries(scopes: tuple[str, ...]) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for scope in scopes:
        raw = (scope or "").strip()
        if not raw:
            continue
        kind, _, target = raw.partition(":")
        if target:
            entries.append((kind.strip().lower(), target.strip()))
        else:
            entries.append(("", raw))
    return entries


def _row_identifier(row: Mapping[str, Any]) -> str:
    for key in ("id", "ID", "key", "token", "document_id", "record_id", "uuid"):
        value = row.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value)
    digest = hashlib.sha256(
        json.dumps(dict(row), ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return digest[:40]


def _json_safe(value: Any) -> Any:
    """把驱动返回的 datetime/Decimal 等转换成可写入 JSONB 的值。"""
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return base64.b64encode(bytes(value)).decode("ascii")
    return value


def _with_source_id(row: dict[str, Any]) -> dict[str, Any]:
    """下游 sink 要求稳定的 source id；没有主键时用行内容哈希兜底。"""
    row = _json_safe(row)
    if not row.get("id") and not row.get("source_id"):
        row = {**row, "source_id": _row_identifier(row)}
    return row



_SQL_LITERAL_OR_COMMENT = re.compile(r"'(?:''|[^'])*'|--[^\n]*|/\*.*?\*/", re.DOTALL)
_SQL_WRITE_KEYWORDS = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|MERGE|REPLACE|UPSERT|CREATE|ALTER|DROP|TRUNCATE|"
    r"GRANT|REVOKE|COMMENT|RENAME|CALL|EXECUTE|PREPARE|DEALLOCATE|COPY|"
    r"VACUUM|ANALYZE|REFRESH|LOCK|SET|RESET|BEGIN|COMMIT|ROLLBACK)\b",
    re.IGNORECASE,
)


def _readonly_query(query: str, *, provider: str) -> str:
    """Validate one SELECT statement; the transaction is separately READ ONLY."""
    raw = (query or "").strip()
    if not raw:
        raise ConnectorError(f"{provider} 只允许单条只读 SELECT 查询")
    scrubbed = _SQL_LITERAL_OR_COMMENT.sub(" ", raw).strip()
    normalized = scrubbed.rstrip().removesuffix(";").strip()
    if ";" in normalized or not re.match(r"^(?:SELECT|WITH)\b", normalized, re.IGNORECASE):
        raise ConnectorError(f"{provider} 只允许单条只读 SELECT 查询，已拒绝写操作或多语句")
    if _SQL_WRITE_KEYWORDS.search(normalized) or re.search(r"\bINTO\b", normalized, re.IGNORECASE):
        raise ConnectorError(f"{provider} 只读约束：已拒绝写操作或数据定义语句")
    return raw

def _qualified(name: str) -> str:
    parts = name.split(".")
    if not all(_IDENTIFIER.fullmatch(part) for part in parts):
        raise ConnectorError(f"数据表名称不合法：{name}")
    return ".".join(f'"{part}"' for part in parts)


class PostgresSourceProvider:
    provider = "postgres"

    async def fetch_page(
        self, *, credential: str, scopes: tuple[str, ...],
        cursor: Mapping[str, Any] | None,
    ) -> ConnectorPage:
        conf = _credential(credential)
        entries = _scope_entries(scopes)
        if not entries:
            raise ConnectorError("PostgreSQL 连接器至少需要一个 scope：table:public.users 或 query:SELECT ...")
        state = dict(cursor or {})
        index = int(state.get("scope_index") or 0)
        offset = int(state.get("offset") or 0)
        if index >= len(entries):
            return ConnectorPage(items=(), next_cursor=None, has_more=False)
        kind, target = entries[index]
        page_size = _page_size(conf)
        if kind == "table" or not kind:
            sql = f"SELECT * FROM {_qualified(target)} ORDER BY 1 LIMIT %s OFFSET %s"
            params: list[Any] = [page_size, offset]
        elif kind == "query":
            base = _readonly_query(target, provider="MySQL").rstrip(";")
            sql = base if re.search(r"\blimit\b", base, re.IGNORECASE) else f"{base} LIMIT %s OFFSET %s"
            params = [] if sql == base else [page_size, offset]
        else:
            raise ConnectorError(f"PostgreSQL 不支持的 scope：{kind or target}")

        try:
            from psycopg import AsyncConnection
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - 依赖缺失时的可读提示
            raise ConnectorError("未安装 psycopg，无法同步 PostgreSQL 数据源") from exc

        dsn = str(conf.get("dsn") or conf.get("url") or "").strip()
        try:
            if dsn:
                connection = await AsyncConnection.connect(dsn, row_factory=dict_row, connect_timeout=15)
            else:
                connection = await AsyncConnection.connect(
                    host=str(conf.get("host") or "127.0.0.1"),
                    port=int(conf.get("port") or 5432),
                    dbname=str(conf.get("database") or conf.get("dbname") or ""),
                    user=str(conf.get("user") or ""),
                    password=str(conf.get("password") or ""),
                    row_factory=dict_row,
                    connect_timeout=15,
                )
            async with connection:
                await connection.execute("SET TRANSACTION READ ONLY")
                cursor_obj = await connection.execute(sql, params)
                rows = await cursor_obj.fetchall()
        except ConnectorError:
            raise
        except Exception as exc:
            raise ConnectorError(f"PostgreSQL 读取失败：{type(exc).__name__}") from exc

        items = tuple(_with_source_id(dict(row)) for row in rows)
        return _advance(items, page_size, entries, index, offset)

    async def revoke(self, *, credential: str) -> None:
        return None


class MySQLSourceProvider:
    provider = "mysql"

    async def fetch_page(
        self, *, credential: str, scopes: tuple[str, ...],
        cursor: Mapping[str, Any] | None,
    ) -> ConnectorPage:
        conf = _credential(credential)
        entries = _scope_entries(scopes)
        if not entries:
            raise ConnectorError("MySQL 连接器至少需要一个 scope：table:users 或 query:SELECT ...")
        state = dict(cursor or {})
        index = int(state.get("scope_index") or 0)
        offset = int(state.get("offset") or 0)
        if index >= len(entries):
            return ConnectorPage(items=(), next_cursor=None, has_more=False)
        kind, target = entries[index]
        page_size = _page_size(conf)
        if kind == "table" or not kind:
            table = _qualified(target).replace('"', "`")
            sql = f"SELECT * FROM {table} LIMIT %s OFFSET %s"
            params: list[Any] = [page_size, offset]
        elif kind == "query":
            base = target.strip().rstrip(";")
            sql = base if re.search(r"\blimit\b", base, re.IGNORECASE) else f"{base} LIMIT %s OFFSET %s"
            params = [] if sql == base else [page_size, offset]
        else:
            raise ConnectorError(f"MySQL 不支持的 scope：{kind or target}")

        def _query() -> list[dict[str, Any]]:
            try:
                import pymysql
            except ImportError as exc:
                raise ConnectorError("未安装 pymysql，无法同步 MySQL 数据源（pip install pymysql）") from exc
            connection = pymysql.connect(
                host=str(conf.get("host") or "127.0.0.1"),
                port=int(conf.get("port") or 3306),
                user=str(conf.get("user") or ""),
                password=str(conf.get("password") or ""),
                database=str(conf.get("database") or conf.get("dbname") or ""),
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
                connect_timeout=15,
            )
            try:
                with connection.cursor() as page:
                    page.execute("START TRANSACTION READ ONLY")
                    page.execute(sql, params)
                    return [dict(row) for row in page.fetchall()]
            finally:
                connection.close()

        try:
            items = tuple(_with_source_id(row) for row in await asyncio.to_thread(_query))
        except ConnectorError:
            raise
        except Exception as exc:
            raise ConnectorError(f"MySQL 读取失败：{type(exc).__name__}") from exc
        return _advance(items, page_size, entries, index, offset)

    async def revoke(self, *, credential: str) -> None:
        return None


def _advance(
    items: tuple[Mapping[str, Any], ...], page_size: int,
    entries: list[tuple[str, str]], index: int, offset: int,
) -> ConnectorPage:
    if len(items) == page_size:
        return ConnectorPage(
            items=items,
            next_cursor={"scope_index": index, "offset": offset + len(items)},
            has_more=True,
        )
    if index + 1 < len(entries):
        return ConnectorPage(
            items=items,
            next_cursor={"scope_index": index + 1, "offset": 0},
            has_more=True,
        )
    return ConnectorPage(items=items, next_cursor=None, has_more=False)


class S3SourceProvider:
    """S3 兼容对象存储（AWS / MinIO）按前缀分页列举对象。"""

    provider = "s3"

    async def fetch_page(
        self, *, credential: str, scopes: tuple[str, ...],
        cursor: Mapping[str, Any] | None,
    ) -> ConnectorPage:
        conf = _credential(credential)
        bucket = str(conf.get("bucket") or "").strip()
        access_key = str(conf.get("access_key") or "").strip()
        secret_key = str(conf.get("secret_key") or "").strip()
        if not bucket or not access_key or not secret_key:
            raise ConnectorError("S3 凭据需要 access_key、secret_key 与 bucket")
        region = str(conf.get("region") or "us-east-1").strip()
        endpoint = str(conf.get("endpoint") or f"https://s3.{region}.amazonaws.com").rstrip("/")
        prefixes = [target for kind, target in _scope_entries(scopes) if target]
        if not prefixes:
            prefixes = [str(conf.get("prefix") or "")]
        state = dict(cursor or {})
        index = int(state.get("scope_index") or 0)
        token = state.get("token")
        if index >= len(prefixes):
            return ConnectorPage(items=(), next_cursor=None, has_more=False)
        page_size = _page_size(conf)
        query: dict[str, str] = {"list-type": "2", "max-keys": str(page_size)}
        if prefixes[index]:
            query["prefix"] = prefixes[index]
        if token:
            query["continuation-token"] = str(token)
        url = f"{endpoint}/{bucket}?{urlencode(sorted(query.items()), quote_via=quote)}"
        body = await self._signed_get(url, access_key, secret_key, region)
        items, truncated, next_token = _parse_s3_listing(body)
        if truncated and next_token:
            return ConnectorPage(
                items=items,
                next_cursor={"scope_index": index, "token": next_token},
                has_more=True,
            )
        if index + 1 < len(prefixes):
            return ConnectorPage(
                items=items,
                next_cursor={"scope_index": index + 1, "token": None},
                has_more=True,
            )
        return ConnectorPage(items=items, next_cursor=None, has_more=False)

    async def _signed_get(self, url: str, access_key: str, secret_key: str, region: str) -> bytes:
        parsed = urlsplit(url)
        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        payload_hash = hashlib.sha256(b"").hexdigest()
        host = parsed.netloc
        canonical_query = "&".join(
            f"{quote(key, safe='-_.~')}={quote(value, safe='-_.~')}"
            for key, value in sorted(_query_pairs(parsed.query))
        )
        canonical_headers = (
            f"host:{host}\n"
            f"x-amz-content-sha256:{payload_hash}\n"
            f"x-amz-date:{amz_date}\n"
        )
        signed_headers = "host;x-amz-content-sha256;x-amz-date"
        canonical_request = "\n".join([
            "GET", quote(parsed.path or "/", safe="/-_.~"), canonical_query,
            canonical_headers, signed_headers, payload_hash,
        ])
        scope = f"{date_stamp}/{region}/s3/aws4_request"
        string_to_sign = "\n".join([
            "AWS4-HMAC-SHA256", amz_date, scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ])
        signing_key = _sign(("AWS4" + secret_key).encode("utf-8"), date_stamp)
        signing_key = _sign(signing_key, region)
        signing_key = _sign(signing_key, "s3")
        signing_key = _sign(signing_key, "aws4_request")
        signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        headers = {
            "Host": host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
            "Authorization": (
                f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
                f"SignedHeaders={signed_headers}, Signature={signature}"
            ),
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                return response.content
        except httpx.HTTPStatusError as exc:
            raise ConnectorError(f"S3 返回 HTTP {exc.response.status_code}") from exc
        except Exception as exc:
            raise ConnectorError(f"S3 请求失败：{type(exc).__name__}") from exc

    async def revoke(self, *, credential: str) -> None:
        return None


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _query_pairs(query: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for part in query.split("&"):
        if not part:
            continue
        key, _, value = part.partition("=")
        pairs.append((key, value))
    return pairs


def _parse_s3_listing(body: bytes) -> tuple[tuple[Mapping[str, Any], ...], bool, str | None]:
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as exc:
        raise ConnectorError("S3 响应不是合法 XML") from exc
    namespace = ""
    if root.tag.startswith("{"):
        namespace = root.tag.split("}")[0] + "}"
    items: list[dict[str, Any]] = []
    for node in root.findall(f"{namespace}Contents"):
        key = node.findtext(f"{namespace}Key") or ""
        if not key:
            continue
        items.append({
            "id": key,
            "key": key,
            "size": int(node.findtext(f"{namespace}Size") or 0),
            "etag": (node.findtext(f"{namespace}ETag") or "").strip('"'),
            "last_modified": node.findtext(f"{namespace}LastModified") or "",
        })
    truncated = (root.findtext(f"{namespace}IsTruncated") or "false").lower() == "true"
    next_token = root.findtext(f"{namespace}NextContinuationToken")
    return tuple(items), truncated, next_token


class FeishuSourceProvider:
    """飞书：云文档目录、多维表格记录与单篇文档正文。"""

    provider = "feishu"
    base_url = "https://open.feishu.cn/open-apis"

    async def fetch_page(
        self, *, credential: str, scopes: tuple[str, ...],
        cursor: Mapping[str, Any] | None,
    ) -> ConnectorPage:
        conf = _credential(credential)
        entries = _scope_entries(scopes)
        if not entries:
            raise ConnectorError("飞书连接器至少需要一个 scope：folder:<token> / bitable:<app>/<table> / doc:<id>")
        app_id = str(conf.get("app_id") or conf.get("appId") or "").strip()
        app_secret = str(conf.get("app_secret") or conf.get("appSecret") or "").strip()
        if not app_id or not app_secret:
            from agent_platform.scheduling.feishu import load_feishu_config
            config = load_feishu_config()
            app_id = app_id or config.app_id
            app_secret = app_secret or config.app_secret
        if not app_id or not app_secret:
            raise ConnectorError("飞书凭据缺失：请在凭据中提供 app_id/app_secret，或配置 FEISHU_APP_ID/FEISHU_APP_SECRET")
        base = str(conf.get("base_url") or self.base_url).rstrip("/")
        async with httpx.AsyncClient(timeout=30.0) as client:
            token = await self._tenant_token(client, base, app_id, app_secret)
            headers = {"Authorization": f"Bearer {token}"}
            state = dict(cursor or {})
            index = int(state.get("scope_index") or 0)
            page_token = state.get("page_token")
            if index >= len(entries):
                return ConnectorPage(items=(), next_cursor=None, has_more=False)
            kind, target = entries[index]
            if kind == "folder":
                url = f"{base}/drive/v1/files"
                params = {"folder_token": target, "page_size": _page_size(conf)}
                if page_token:
                    params["page_token"] = str(page_token)
            elif kind == "bitable":
                app_token, _, table_id = target.partition("/")
                if not app_token or not table_id:
                    raise ConnectorError("bitable scope 格式应为 bitable:<app_token>/<table_id>")
                url = f"{base}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
                params = {"page_size": min(_page_size(conf), 500)}
                if page_token:
                    params["page_token"] = str(page_token)
            elif kind == "doc":
                url = f"{base}/docx/v1/documents/{target}/raw_content"
                params = {}
            else:
                raise ConnectorError(f"飞书不支持的 scope：{kind or target}")
            try:
                response = await client.get(url, headers=headers, params=params)
                data = response.json()
            except Exception as exc:
                raise ConnectorError(f"飞书请求失败：{type(exc).__name__}") from exc
        if data.get("code") != 0:
            raise ConnectorError(f"飞书接口返回错误：code={data.get('code')} msg={data.get('msg')}")
        payload = data.get("data") or {}
        if kind == "doc":
            text = payload.get("content") or ""
            items = ({"id": target, "document_id": target, "content": text},)
            return ConnectorPage(items=items, next_cursor=None, has_more=False)
        if kind == "folder":
            raw_items = payload.get("files") or []
            items = tuple({
                "id": str(item.get("token") or item.get("file_token") or ""),
                "name": item.get("name"),
                "type": item.get("type"),
                "url": item.get("url"),
                "token": item.get("token") or item.get("file_token"),
            } for item in raw_items if item.get("token") or item.get("file_token"))
        else:
            raw_items = payload.get("items") or []
            items = tuple({
                "id": str(item.get("record_id") or ""),
                "record_id": item.get("record_id"),
                "fields": item.get("fields") or {},
            } for item in raw_items if item.get("record_id"))
        has_more = bool(payload.get("has_more"))
        next_token = payload.get("page_token") or payload.get("next_page_token")
        if has_more and next_token:
            return ConnectorPage(
                items=items,
                next_cursor={"scope_index": index, "page_token": next_token},
                has_more=True,
            )
        if index + 1 < len(entries):
            return ConnectorPage(
                items=items, next_cursor={"scope_index": index + 1, "page_token": None},
                has_more=True,
            )
        return ConnectorPage(items=items, next_cursor=None, has_more=False)

    async def _tenant_token(
        self, client: httpx.AsyncClient, base: str, app_id: str, app_secret: str,
    ) -> str:
        response = await client.post(
            f"{base}/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
        )
        data = response.json()
        token = data.get("tenant_access_token")
        if data.get("code") != 0 or not token:
            raise ConnectorError(f"飞书鉴权失败：code={data.get('code')} msg={data.get('msg')}")
        return str(token)

    async def revoke(self, *, credential: str) -> None:
        return None


class NotionSourceProvider:
    """Notion 数据库查询与单页读取。"""

    provider = "notion"
    base_url = "https://api.notion.com/v1"

    async def fetch_page(
        self, *, credential: str, scopes: tuple[str, ...],
        cursor: Mapping[str, Any] | None,
    ) -> ConnectorPage:
        conf = _credential(credential)
        token = str(conf.get("token") or conf.get("api_key") or conf.get("dsn") or "").strip()
        if not token:
            raise ConnectorError("Notion 凭据需要 token")
        entries = _scope_entries(scopes)
        if not entries:
            raise ConnectorError("Notion 连接器至少需要一个 scope：database:<id> 或 page:<id>")
        state = dict(cursor or {})
        index = int(state.get("scope_index") or 0)
        if index >= len(entries):
            return ConnectorPage(items=(), next_cursor=None, has_more=False)
        kind, target = entries[index]
        headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": str(conf.get("notion_version") or "2022-06-28"),
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                if kind == "database":
                    response = await client.post(
                        f"{self.base_url}/databases/{target}/query",
                        headers=headers,
                        json={
                            "page_size": min(_page_size(conf), 100),
                            **({"start_cursor": state["page_token"]} if state.get("page_token") else {}),
                        },
                    )
                elif kind == "page":
                    response = await client.get(f"{self.base_url}/pages/{target}", headers=headers)
                else:
                    raise ConnectorError(f"Notion 不支持的 scope：{kind or target}")
                response.raise_for_status()
                data = response.json()
        except ConnectorError:
            raise
        except httpx.HTTPStatusError as exc:
            raise ConnectorError(f"Notion 返回 HTTP {exc.response.status_code}") from exc
        except Exception as exc:
            raise ConnectorError(f"Notion 请求失败：{type(exc).__name__}") from exc
        if kind == "page":
            return ConnectorPage(items=(data,), next_cursor=None, has_more=False)
        items = tuple(data.get("results") or [])
        if data.get("has_more") and data.get("next_cursor"):
            return ConnectorPage(
                items=items,
                next_cursor={"scope_index": index, "page_token": data["next_cursor"]},
                has_more=True,
            )
        if index + 1 < len(entries):
            return ConnectorPage(
                items=items, next_cursor={"scope_index": index + 1}, has_more=True,
            )
        return ConnectorPage(items=items, next_cursor=None, has_more=False)

    async def revoke(self, *, credential: str) -> None:
        return None


class HttpJsonSourceProvider:
    """通用 HTTP JSON 数据源：按 path/url scope 拉取并自动识别分页字段。"""

    provider = "http"

    async def fetch_page(
        self, *, credential: str, scopes: tuple[str, ...],
        cursor: Mapping[str, Any] | None,
    ) -> ConnectorPage:
        conf = _credential(credential)
        base = str(conf.get("base_url") or "").rstrip("/")
        entries = _scope_entries(scopes)
        if not entries:
            raise ConnectorError("HTTP 连接器至少需要一个 scope：path:/api/items 或 url:https://...")
        state = dict(cursor or {})
        index = int(state.get("scope_index") or 0)
        if index >= len(entries):
            return ConnectorPage(items=(), next_cursor=None, has_more=False)
        kind, target = entries[index]
        if kind == "url":
            url = target
        elif kind in {"path", ""}:
            if not base:
                raise ConnectorError("HTTP 凭据需要 base_url")
            url = f"{base}/{target.lstrip('/')}"
        else:
            raise ConnectorError(f"HTTP 不支持的 scope：{kind}")
        headers = {str(k): str(v) for k, v in (conf.get("headers") or {}).items()}
        token = str(conf.get("token") or "").strip()
        if token:
            headers.setdefault("Authorization", f"Bearer {token}")
        params: dict[str, Any] = dict(conf.get("params") or {})
        cursor_param = str(conf.get("cursor_param") or "cursor")
        if state.get("page_token"):
            params[cursor_param] = state["page_token"]
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=headers, params=params)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            raise ConnectorError(f"HTTP 数据源返回 HTTP {exc.response.status_code}") from exc
        except Exception as exc:
            raise ConnectorError(f"HTTP 数据源请求失败：{type(exc).__name__}") from exc
        if isinstance(data, list):
            items, next_token = data, None
        elif isinstance(data, dict):
            raw = data.get("items") or data.get("data") or data.get("results") or []
            items = raw if isinstance(raw, list) else []
            next_token = data.get("next") or data.get("next_cursor") or data.get("cursor")
        else:
            raise ConnectorError("HTTP 数据源返回的 JSON 结构不受支持")
        normalized = tuple(_with_source_id(dict(item)) for item in items if isinstance(item, dict))
        if next_token:
            return ConnectorPage(
                items=normalized,
                next_cursor={"scope_index": index, "page_token": str(next_token)},
                has_more=True,
            )
        if index + 1 < len(entries):
            return ConnectorPage(
                items=normalized, next_cursor={"scope_index": index + 1}, has_more=True,
            )
        return ConnectorPage(items=normalized, next_cursor=None, has_more=False)

    async def revoke(self, *, credential: str) -> None:
        return None


def build_default_providers() -> dict[str, Any]:
    """注册全部内置 provider；未配置凭据时仍可创建连接，同步时给出中文错误。"""
    return {
        "postgres": PostgresSourceProvider(),
        "mysql": MySQLSourceProvider(),
        "s3": S3SourceProvider(),
        "feishu": FeishuSourceProvider(),
        "notion": NotionSourceProvider(),
        "http": HttpJsonSourceProvider(),
    }
