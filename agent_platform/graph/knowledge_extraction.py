"""知识库文档 → 图谱实体/关系抽取。

流程：读取当前租户可见的知识文档与切片 → 调用本地 Ollama 抽取概念/人物/
组织/主题 → 写入 graph_entities / graph_relations。抽取以后台作业运行，
作业状态写入 platform_graph_extraction_jobs。
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence

import httpx

from agent_platform.knowledge.ollama import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    OllamaUnavailable,
    _OllamaClient,
    ollama_config_from_env,
)

ENTITY_TYPES = ("concept", "person", "organization", "topic")
RELATION_TYPES = ("related_to",)
MAX_ENTITIES = 25
MAX_RELATIONS = 30
MAX_CHUNKS = 10
MAX_DOCUMENT_CHARS = 6000
JOB_STATUSES = ("queued", "running", "succeeded", "failed")

SYSTEM_PROMPT = (
    "你是知识图谱抽取器。只依据给定的文档内容抽取实体与关系，"
    "不得引入文档之外的信息。只输出一个 JSON 对象，不要解释或使用 Markdown。"
)

_TYPE_ALIASES = {
    "concept": "concept", "概念": "concept", "术语": "concept",
    "person": "person", "人物": "person", "人名": "person",
    "organization": "organization", "org": "organization",
    "组织": "organization", "机构": "organization", "公司": "organization",
    "topic": "topic", "主题": "topic", "话题": "topic",
}


@dataclass(frozen=True)
class ExtractionDocument:
    document_id: str
    version: int
    title: str
    project_id: str | None
    text: str
    chunk_count: int


@dataclass(frozen=True)
class ExtractedEntity:
    name: str
    entity_type: str
    description: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class ExtractedRelation:
    source: str
    target: str
    relation_type: str


@dataclass(frozen=True)
class ExtractionDraft:
    entities: tuple[ExtractedEntity, ...]
    relations: tuple[ExtractedRelation, ...]


class GraphDocumentExtractor(Protocol):
    async def extract(self, *, title: str, text: str) -> ExtractionDraft: ...


class OllamaGraphExtractor(_OllamaClient):
    """使用本地 Ollama 抽取实体与关系，返回结构化草稿。"""

    def __init__(
        self, *, base_url: str = DEFAULT_BASE_URL, model: str = DEFAULT_MODEL,
        timeout_seconds: float = 240.0, keep_alive: str = "30s",
        log_path: str | os.PathLike[str] | None = None,
    ) -> None:
        super().__init__(base_url=base_url, model=model, log_path=log_path)
        self.timeout_seconds = float(timeout_seconds)
        self.keep_alive = keep_alive

    async def extract(self, *, title: str, text: str) -> ExtractionDraft:
        await self._ensure_ready()
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": self._prompt(title=title, text=text)},
            ],
            "stream": False,
            "format": "json",
            "keep_alive": self.keep_alive,
            "options": {"temperature": 0},
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(f"{self.base_url}/api/chat", json=payload)
                if response.status_code == 404:
                    raise OllamaUnavailable(
                        f"本地 Ollama 中找不到模型 {self.model}，请先执行 ollama pull {self.model}"
                    )
                response.raise_for_status()
                data = response.json()
        except OllamaUnavailable:
            raise
        except httpx.HTTPError as exc:
            raise OllamaUnavailable(f"本地模型调用失败：{exc}") from exc
        message = data.get("message") if isinstance(data, dict) else None
        content = message.get("content") if isinstance(message, Mapping) else None
        if not isinstance(content, str) or not content.strip():
            raise OllamaUnavailable("本地模型返回了空响应")
        return parse_extraction(content)

    @staticmethod
    def _prompt(*, title: str, text: str) -> str:
        return (
            "请从下面文档中抽取实体和关系。\n"
            "实体类型只能是：concept（概念/术语）、person（人物）、"
            "organization（组织/机构）、topic（主题）。\n"
            "关系类型只能是 related_to（实体之间相关）。\n"
            "输出 JSON 格式：\n"
            '{"entities":[{"name":"实体名称","type":"concept","description":"一句话说明","aliases":["别名"]}],'
            '"relations":[{"source":"实体名称","target":"实体名称","type":"related_to"}]}\n'
            f"要求：名称必须出现在文档中；最多 {MAX_ENTITIES} 个实体、{MAX_RELATIONS} 条关系；"
            "没有结果时返回空数组。\n"
            f"文档标题：{title}\n文档内容：\n{text}"
        )


def _strip_fences(value: str) -> str:
    text = value.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    return text.strip()


def parse_extraction(content: str) -> ExtractionDraft:
    try:
        payload = json.loads(_strip_fences(content))
    except json.JSONDecodeError as exc:
        raise OllamaUnavailable("本地模型未返回可解析的 JSON 结果") from exc
    if not isinstance(payload, Mapping):
        raise OllamaUnavailable("本地模型返回的 JSON 不是对象")
    raw_entities = payload.get("entities")
    raw_relations = payload.get("relations")
    if not isinstance(raw_entities, list) or not isinstance(raw_relations, list):
        raise OllamaUnavailable("本地模型返回缺少 entities 或 relations 数组")

    entities: list[ExtractedEntity] = []
    seen: set[tuple[str, str]] = set()
    for item in raw_entities[:MAX_ENTITIES * 2]:
        if not isinstance(item, Mapping):
            continue
        name = item.get("name")
        entity_type = _TYPE_ALIASES.get(
            str(item.get("type") or "").strip().lower()
        )
        if not isinstance(name, str) or not name.strip() or entity_type is None:
            continue
        normalized = name.strip()[:200]
        key = (entity_type, normalized.casefold())
        if key in seen:
            continue
        seen.add(key)
        description = item.get("description")
        aliases = item.get("aliases")
        entities.append(
            ExtractedEntity(
                name=normalized,
                entity_type=entity_type,
                description=description.strip()[:2000] if isinstance(description, str) else "",
                aliases=tuple(
                    value.strip()[:200] for value in (aliases if isinstance(aliases, list) else [])
                    if isinstance(value, str) and value.strip()
                )[:10],
            )
        )
        if len(entities) >= MAX_ENTITIES:
            break

    by_name = {entity.name.casefold(): entity for entity in entities}
    relations: list[ExtractedRelation] = []
    seen_relations: set[tuple[str, str]] = set()
    for item in raw_relations[:MAX_RELATIONS * 2]:
        if not isinstance(item, Mapping):
            continue
        relation_type = str(item.get("type") or "related_to").strip().lower()
        if relation_type not in RELATION_TYPES:
            continue
        source = item.get("source")
        target = item.get("target")
        if not isinstance(source, str) or not isinstance(target, str):
            continue
        source_entity = by_name.get(source.strip().casefold())
        target_entity = by_name.get(target.strip().casefold())
        if source_entity is None or target_entity is None:
            continue
        if source_entity.name.casefold() == target_entity.name.casefold():
            continue
        key = (source_entity.name.casefold(), target_entity.name.casefold())
        if key in seen_relations:
            continue
        seen_relations.add(key)
        relations.append(
            ExtractedRelation(
                source=source_entity.name,
                target=target_entity.name,
                relation_type=relation_type,
            )
        )
        if len(relations) >= MAX_RELATIONS:
            break
    return ExtractionDraft(tuple(entities), tuple(relations))


def _entity_id(tenant_id: str, entity_type: str, name: str) -> str:
    digest = hashlib.sha256(
        f"{tenant_id}|{entity_type}|{name.casefold()}".encode("utf-8")
    ).hexdigest()[:32]
    return f"ent:{digest}"


def _relation_id(tenant_id: str, relation_type: str, source: str, target: str) -> str:
    digest = hashlib.sha256(
        f"{tenant_id}|{relation_type}|{source}|{target}".encode("utf-8")
    ).hexdigest()[:32]
    return f"rel:{digest}"


class KnowledgeGraphExtractionService:
    def __init__(
        self, *, connection_factory: Callable[[], Any],
        extractor: GraphDocumentExtractor,
    ) -> None:
        self.connection_factory = connection_factory
        self.extractor = extractor

    async def list_documents(
        self, *, tenant_id: str, document_ids: Sequence[str], limit: int,
        project_ids: Sequence[str], user_id: str | None,
    ) -> list[ExtractionDocument]:
        clauses = ["tenant_id=%s", "deleted_at IS NULL"]
        params: list[Any] = [tenant_id]
        if document_ids:
            clauses.append("document_id::text = ANY(%s)")
            params.append(list(document_ids))
        scope = ["scope='tenant'"]
        if project_ids:
            scope.append("(scope='project' AND project_id = ANY(%s))")
            params.append(list(project_ids))
        if user_id:
            scope.append("(scope='private' AND owner_user_id=%s)")
            params.append(user_id)
        clauses.append("(" + " OR ".join(scope) + ")")
        sampled = limit * 3
        params.append(sampled)

        connection = await self.connection_factory()
        async with connection:
            cursor = await connection.execute(
                f"""
                SELECT document_id::text AS document_id, version, title, project_id
                FROM (
                    SELECT DISTINCT ON (document_id)
                           document_id, version, title, project_id, created_at
                    FROM knowledge_documents
                    WHERE {' AND '.join(clauses)}
                    ORDER BY document_id, version DESC
                ) latest
                ORDER BY created_at DESC
                LIMIT %s
                """,
                tuple(params),
            )
            rows = await cursor.fetchall()

        documents: list[ExtractionDocument] = []
        for row in rows:
            record = dict(row)
            chunks = await self._document_chunks(
                tenant_id=tenant_id,
                document_id=record["document_id"],
                version=int(record["version"]),
            )
            if not chunks:
                continue
            text, chunk_count = chunks
            documents.append(
                ExtractionDocument(
                    document_id=record["document_id"],
                    version=int(record["version"]),
                    title=str(record["title"]),
                    project_id=record.get("project_id"),
                    text=text,
                    chunk_count=chunk_count,
                )
            )
            if len(documents) >= limit:
                break
        return documents

    async def _document_chunks(
        self, *, tenant_id: str, document_id: str, version: int,
    ) -> tuple[str, int] | None:
        connection = await self.connection_factory()
        async with connection:
            cursor = await connection.execute(
                """
                SELECT body FROM knowledge_chunks
                WHERE tenant_id=%s AND document_id=%s AND document_version=%s
                ORDER BY ordinal
                LIMIT %s
                """,
                (tenant_id, document_id, version, MAX_CHUNKS),
            )
            rows = await cursor.fetchall()
        bodies = [str(dict(row)["body"]) for row in rows]
        if not bodies:
            return None
        text = "\n".join(bodies)[:MAX_DOCUMENT_CHARS]
        return text, len(bodies)

    async def create_job(
        self, *, tenant_id: str, document_ids: Sequence[str], total: int,
    ) -> str:
        from psycopg.types.json import Jsonb

        job_id = str(uuid.uuid4())
        connection = await self.connection_factory()
        async with connection:
            await connection.execute(
                """
                INSERT INTO platform_graph_extraction_jobs
                    (tenant_id, job_id, status, document_ids, total)
                VALUES (%s, %s, 'queued', %s, %s)
                """,
                (tenant_id, job_id, Jsonb(list(document_ids)), total),
            )
        return job_id

    async def run_job(
        self, *, tenant_id: str, job_id: str, documents: Sequence[ExtractionDocument],
    ) -> None:
        await self._update_job(tenant_id, job_id, status="running")
        processed = 0
        entity_count = 0
        relation_count = 0
        skipped = 0
        try:
            for document in documents:
                try:
                    draft = await self.extractor.extract(
                        title=document.title, text=document.text,
                    )
                except OllamaUnavailable:
                    raise
                except Exception:
                    skipped += 1
                    processed += 1
                    await self._update_job(
                        tenant_id, job_id, processed=processed, skipped=skipped,
                    )
                    continue
                entities, relations = await self._persist(
                    tenant_id=tenant_id, document=document, draft=draft,
                )
                entity_count += entities
                relation_count += relations
                processed += 1
                await self._update_job(
                    tenant_id, job_id, processed=processed,
                    entity_count=entity_count, relation_count=relation_count,
                    skipped=skipped,
                )
            await self._update_job(
                tenant_id, job_id, status="succeeded", processed=processed,
                entity_count=entity_count, relation_count=relation_count,
                skipped=skipped,
            )
        except Exception as exc:  # noqa: BLE001 - 作业失败必须落库可见
            message = str(exc) or exc.__class__.__name__
            await self._update_job(
                tenant_id, job_id, status="failed", error=message[:2000],
                processed=processed, entity_count=entity_count,
                relation_count=relation_count, skipped=skipped,
            )

    async def _persist(
        self, *, tenant_id: str, document: ExtractionDocument, draft: ExtractionDraft,
    ) -> tuple[int, int]:
        from psycopg.types.json import Jsonb

        document_entity_id = f"doc:{document.document_id}"
        source_version = max(1, document.version)
        written_entities = 0
        written_relations = 0

        connection = await self.connection_factory()
        async with connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    INSERT INTO graph_entities (
                        tenant_id, entity_id, entity_type, entity_name, attributes,
                        project_id, source_domain, source_type, source_id, source_version
                    ) VALUES (%s, %s, 'document', %s, %s, %s, 'knowledge', 'document', %s, %s)
                    ON CONFLICT (tenant_id, entity_id) DO UPDATE SET
                        entity_name=EXCLUDED.entity_name,
                        attributes=EXCLUDED.attributes,
                        project_id=EXCLUDED.project_id,
                        source_domain=EXCLUDED.source_domain,
                        source_type=EXCLUDED.source_type,
                        source_id=EXCLUDED.source_id,
                        source_version=EXCLUDED.source_version,
                        updated_at=now()
                    """,
                    (
                        tenant_id, document_entity_id, document.title[:500],
                        Jsonb({"title": document.title}), document.project_id,
                        document.document_id, source_version,
                    ),
                )

                entity_ids: dict[str, str] = {}
                for entity in draft.entities:
                    entity_id = _entity_id(tenant_id, entity.entity_type, entity.name)
                    entity_ids[entity.name.casefold()] = entity_id
                    attributes: dict[str, Any] = {}
                    if entity.description:
                        attributes["description"] = entity.description
                    if entity.aliases:
                        attributes["aliases"] = list(entity.aliases)
                    await connection.execute(
                        """
                        INSERT INTO graph_entities (
                            tenant_id, entity_id, entity_type, entity_name,
                            attributes, project_id
                        ) VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (tenant_id, entity_id) DO UPDATE SET
                            entity_name=EXCLUDED.entity_name,
                            attributes=EXCLUDED.attributes,
                            project_id=EXCLUDED.project_id,
                            updated_at=now()
                        """,
                        (
                            tenant_id, entity_id, entity.entity_type,
                            entity.name[:500], Jsonb(attributes), document.project_id,
                        ),
                    )
                    written_entities += 1
                    relation_id = _relation_id(
                        tenant_id, "mentions", document_entity_id, entity_id,
                    )
                    await connection.execute(
                        """
                        INSERT INTO graph_relations (
                            tenant_id, relation_id, relation_type,
                            source_entity_id, target_entity_id,
                            source_entity_type, target_entity_type,
                            attributes, project_id
                        ) VALUES (%s, %s, 'mentions', %s, %s, 'document', %s, %s, %s)
                        ON CONFLICT (tenant_id, relation_id) DO UPDATE SET
                            updated_at=now()
                        """,
                        (
                            tenant_id, relation_id, document_entity_id, entity_id,
                            entity.entity_type, Jsonb({}), document.project_id,
                        ),
                    )
                    written_relations += 1

                for relation in draft.relations:
                    source_id = entity_ids.get(relation.source.casefold())
                    target_id = entity_ids.get(relation.target.casefold())
                    if source_id is None or target_id is None or source_id == target_id:
                        continue
                    source_type = _entity_type_of(draft, relation.source)
                    target_type = _entity_type_of(draft, relation.target)
                    relation_id = _relation_id(
                        tenant_id, relation.relation_type, source_id, target_id,
                    )
                    await connection.execute(
                        """
                        INSERT INTO graph_relations (
                            tenant_id, relation_id, relation_type,
                            source_entity_id, target_entity_id,
                            source_entity_type, target_entity_type,
                            attributes, project_id
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (tenant_id, relation_id) DO UPDATE SET
                            updated_at=now()
                        """,
                        (
                            tenant_id, relation_id, relation.relation_type,
                            source_id, target_id, source_type, target_type,
                            Jsonb({}), document.project_id,
                        ),
                    )
                    written_relations += 1
        return written_entities, written_relations

    async def _update_job(self, tenant_id: str, job_id: str, **fields: Any) -> None:
        allowed = {
            "status", "processed", "entity_count", "relation_count", "skipped", "error",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        assignments = ", ".join(f"{key}=%s" for key in updates)
        params = list(updates.values()) + [tenant_id, job_id]
        connection = await self.connection_factory()
        async with connection:
            await connection.execute(
                f"""
                UPDATE platform_graph_extraction_jobs
                SET {assignments}, updated_at=now()
                WHERE tenant_id=%s AND job_id=%s
                """,
                tuple(params),
            )

    async def list_jobs(self, *, tenant_id: str, limit: int = 10) -> list[dict[str, Any]]:
        connection = await self.connection_factory()
        async with connection:
            cursor = await connection.execute(
                """
                SELECT job_id, status, total, processed, entity_count,
                       relation_count, skipped, error, created_at, updated_at
                FROM platform_graph_extraction_jobs
                WHERE tenant_id=%s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (tenant_id, limit),
            )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_job(self, *, tenant_id: str, job_id: str) -> dict[str, Any] | None:
        connection = await self.connection_factory()
        async with connection:
            cursor = await connection.execute(
                """
                SELECT job_id, status, total, processed, entity_count,
                       relation_count, skipped, error, created_at, updated_at
                FROM platform_graph_extraction_jobs
                WHERE tenant_id=%s AND job_id=%s
                """,
                (tenant_id, job_id),
            )
            row = await cursor.fetchone()
        return dict(row) if row is not None else None


def _entity_type_of(draft: ExtractionDraft, name: str) -> str:
    for entity in draft.entities:
        if entity.name.casefold() == name.casefold():
            return entity.entity_type
    return "concept"


def build_extractor_from_env() -> OllamaGraphExtractor:
    config = ollama_config_from_env()
    timeout = os.environ.get("GRAPH_EXTRACTION_TIMEOUT", "240").strip()
    try:
        timeout_seconds = float(timeout)
    except ValueError:
        timeout_seconds = 240.0
    return OllamaGraphExtractor(
        base_url=config["base_url"],
        model=config["model"],
        timeout_seconds=timeout_seconds,
        keep_alive=config["keep_alive"],
        log_path=config["log_path"],
    )
