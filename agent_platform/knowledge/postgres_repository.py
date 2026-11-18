"""PostgreSQL repository with tenant/ACL-first hybrid retrieval."""
from __future__ import annotations

from typing import Any, Callable, Sequence

from agent_platform.knowledge.contracts import (
    Document,
    DocumentChunk,
    KnowledgeCollection,
    KnowledgeIndex,
    KnowledgePrincipal,
    KnowledgeScope,
    RetrievalHit,
)

ConnectionFactory=Callable[[],Any]
def _jsonb(value: Any)->Any:
    try:from psycopg.types.json import Jsonb  # noqa: I001
    except ImportError as exc:raise RuntimeError("Install psycopg[binary,pool]") from exc
    return Jsonb(value)
class PostgresKnowledgeRepository:
    def __init__(self,connection_factory: ConnectionFactory,*,embedding_model: str="default",embedding_model_version: str="1",embedding_dimensions: int=1536)->None:
        if not embedding_model.strip() or not embedding_model_version.strip():
            raise ValueError("embedding model and version are required")
        if isinstance(embedding_dimensions, bool) or not isinstance(embedding_dimensions, int) or embedding_dimensions < 1:
            raise ValueError("embedding_dimensions must be a positive integer")
        self._connect=connection_factory
        self.embedding_model=embedding_model
        self.embedding_model_version=embedding_model_version
        self.embedding_dimensions=embedding_dimensions
    async def create_collection(self, collection: KnowledgeCollection) -> KnowledgeCollection:
        conn = await self._connect()
        async with conn:
            async with conn.transaction():
                await conn.execute(
                    """INSERT INTO knowledge_collections
                    (tenant_id,collection_id,name,description,project_id,metadata)
                    VALUES(%s,%s,%s,%s,%s,%s)""",
                    (collection.tenant_id, collection.collection_id, collection.name,
                     collection.description, collection.project_id, _jsonb(collection.metadata)),
                )
                index = KnowledgeIndex(
                    tenant_id=collection.tenant_id, collection_id=collection.collection_id,
                )
                await conn.execute(
                    """INSERT INTO knowledge_indexes
                    (tenant_id,index_id,collection_id,name,index_type,embedding_model,dimensions,status)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (index.tenant_id,index.index_id,index.collection_id,index.name,
                     index.index_type,index.embedding_model,index.dimensions,index.status),
                )
        return collection

    async def get_collection(self, tenant_id: str, collection_id: str) -> KnowledgeCollection | None:
        conn = await self._connect()
        async with conn:
            cur = await conn.execute(
                """SELECT * FROM knowledge_collections
                WHERE tenant_id=%s AND collection_id=%s AND deleted_at IS NULL""",
                (tenant_id, collection_id),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return KnowledgeCollection(
            tenant_id=row["tenant_id"], collection_id=str(row["collection_id"]),
            name=row["name"], description=row["description"],
            project_id=row["project_id"], metadata=dict(row["metadata"] or {}),
            created_at=row["created_at"], updated_at=row["updated_at"],
            deleted_at=row["deleted_at"],
        )

    async def list_collections(self, principal: KnowledgePrincipal, limit: int = 100) -> list[KnowledgeCollection]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        conn = await self._connect()
        async with conn:
            cur = await conn.execute(
                """SELECT * FROM knowledge_collections
                WHERE tenant_id=%s AND deleted_at IS NULL
                AND (project_id IS NULL OR project_id=ANY(%s))
                ORDER BY created_at DESC LIMIT %s""",
                (principal.tenant_id, list(principal.project_ids | principal.manage_project_ids), limit),
            )
            rows = await cur.fetchall()
        return [KnowledgeCollection(
            tenant_id=row["tenant_id"], collection_id=str(row["collection_id"]),
            name=row["name"], description=row["description"],
            project_id=row["project_id"], metadata=dict(row["metadata"] or {}),
            created_at=row["created_at"], updated_at=row["updated_at"],
            deleted_at=row["deleted_at"],
        ) for row in rows]

    async def update_collection(
        self, tenant_id: str, collection_id: str, *, name: str,
        description: str, metadata: dict,
    ) -> KnowledgeCollection | None:
        conn = await self._connect()
        async with conn:
            async with conn.transaction():
                cur = await conn.execute(
                    """UPDATE knowledge_collections
                    SET name=%s, description=%s, metadata=%s, updated_at=now()
                    WHERE tenant_id=%s AND collection_id=%s AND deleted_at IS NULL
                    RETURNING *""",
                    (name, description, _jsonb(metadata), tenant_id, collection_id),
                )
                row = await cur.fetchone()
        if row is None:
            return None
        return KnowledgeCollection(
            tenant_id=row["tenant_id"], collection_id=str(row["collection_id"]),
            name=row["name"], description=row["description"],
            project_id=row["project_id"], metadata=dict(row["metadata"] or {}),
            created_at=row["created_at"], updated_at=row["updated_at"],
            deleted_at=row["deleted_at"],
        )

    async def delete_collection(self, tenant_id: str, collection_id: str) -> bool:
        conn = await self._connect()
        async with conn:
            async with conn.transaction():
                cur = await conn.execute(
                    """SELECT collection_id FROM knowledge_collections
                    WHERE tenant_id=%s AND collection_id=%s AND deleted_at IS NULL
                    FOR UPDATE""",
                    (tenant_id, collection_id),
                )
                if await cur.fetchone() is None:
                    return False
                cur = await conn.execute(
                    """SELECT 1 FROM knowledge_documents
                    WHERE tenant_id=%s AND collection_id=%s AND deleted_at IS NULL
                    LIMIT 1""",
                    (tenant_id, collection_id),
                )
                if await cur.fetchone() is not None:
                    raise ValueError("collection contains active documents")
                cur = await conn.execute(
                    """UPDATE knowledge_collections SET deleted_at=now(), updated_at=now()
                    WHERE tenant_id=%s AND collection_id=%s AND deleted_at IS NULL""",
                    (tenant_id, collection_id),
                )
                return cur.rowcount == 1

    async def create_index(self, index: KnowledgeIndex) -> KnowledgeIndex:
        if await self.get_collection(index.tenant_id, index.collection_id) is None:
            raise ValueError("collection not found")
        conn = await self._connect()
        async with conn:
            async with conn.transaction():
                await conn.execute(
                    """INSERT INTO knowledge_indexes
                    (tenant_id,index_id,collection_id,name,index_type,embedding_model,dimensions,status)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (index.tenant_id,index.index_id,index.collection_id,index.name,
                     index.index_type,index.embedding_model,index.dimensions,index.status),
                )
        return index

    async def list_indexes(self, tenant_id: str, collection_id: str) -> list[KnowledgeIndex]:
        conn = await self._connect()
        async with conn:
            cur = await conn.execute(
                """SELECT i.* FROM knowledge_indexes i
                JOIN knowledge_collections c ON c.tenant_id=i.tenant_id
                AND c.collection_id=i.collection_id
                WHERE i.tenant_id=%s AND i.collection_id=%s AND c.deleted_at IS NULL
                ORDER BY i.created_at""",
                (tenant_id, collection_id),
            )
            rows = await cur.fetchall()
        return [KnowledgeIndex(
            tenant_id=row["tenant_id"], collection_id=str(row["collection_id"]),
            index_id=str(row["index_id"]), name=row["name"],
            index_type=row["index_type"], embedding_model=row["embedding_model"],
            dimensions=row["dimensions"], status=row["status"], created_at=row["created_at"],
        ) for row in rows]

    async def save_document(self,document: Document,chunks: Sequence[DocumentChunk])->bool:
        conn=await self._connect()
        async with conn:
            async with conn.transaction():
                cur=await conn.execute("SELECT 1 FROM knowledge_documents WHERE tenant_id=%s AND document_id=%s AND version=%s",(document.tenant_id,document.document_id,document.version))
                if await cur.fetchone():return False
                await conn.execute("""INSERT INTO knowledge_documents(tenant_id,document_id,version,title,source_uri,content_hash,scope,project_id,owner_user_id,media_type,metadata,collection_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",(document.tenant_id,document.document_id,document.version,document.title,document.source_uri,document.content_hash,document.scope.value,document.project_id,document.owner_user_id,document.media_type,_jsonb(document.metadata),document.collection_id))
                for c in chunks:
                    await conn.execute("""INSERT INTO knowledge_chunks(tenant_id,document_id,document_version,chunk_id,ordinal,body,content_hash,start_offset,end_offset,page_number,metadata) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",(c.tenant_id,c.document_id,c.document_version,c.chunk_id,c.ordinal,c.text,c.content_hash,c.start_offset,c.end_offset,c.page_number,_jsonb(c.metadata)))
        return True
    async def save_embeddings(self,*,tenant_id: str,chunks: Sequence[DocumentChunk],vectors: Sequence[Sequence[float]])->None:
        if len(chunks)!=len(vectors):raise ValueError("embedding count must match chunk count")
        conn=await self._connect()
        async with conn:
            async with conn.transaction():
                for chunk,vector in zip(chunks,vectors):
                    literal="["+",".join(str(float(x)) for x in vector)+"]"
                    if len(vector) != self.embedding_dimensions:
                        raise ValueError("embedding dimension mismatch")
                    await conn.execute("""INSERT INTO knowledge_embedding_models(embedding_model,model_version,dimensions)
                    VALUES(%s,%s,%s) ON CONFLICT(embedding_model,model_version) DO UPDATE SET dimensions=EXCLUDED.dimensions""",
                    (self.embedding_model,self.embedding_model_version,self.embedding_dimensions))
                    await conn.execute("""INSERT INTO knowledge_embeddings(tenant_id,chunk_id,embedding_model,embedding,model_version,dimensions)
                    VALUES(%s,%s,%s,%s::vector,%s,%s)
                    ON CONFLICT(tenant_id,chunk_id,embedding_model) DO UPDATE SET embedding=EXCLUDED.embedding,
                    model_version=EXCLUDED.model_version,dimensions=EXCLUDED.dimensions,created_at=now()""",
                    (tenant_id,chunk.chunk_id,self.embedding_model,literal,self.embedding_model_version,self.embedding_dimensions))
    async def get_document(self,tenant_id: str,document_id: str,version: int|None=None)->Document|None:
        conn=await self._connect()
        async with conn:
            sql="SELECT * FROM knowledge_documents WHERE tenant_id=%s AND document_id=%s AND deleted_at IS NULL";params:list[Any]=[tenant_id,document_id]
            if version is not None:sql+=" AND version=%s";params.append(version)
            sql+=" ORDER BY version DESC LIMIT 1";cur=await conn.execute(sql,tuple(params));row=await cur.fetchone()
        if not row:return None
        return Document(tenant_id=row["tenant_id"],document_id=str(row["document_id"]),version=row["version"],title=row["title"],source_uri=row["source_uri"],content_hash=row["content_hash"],scope=KnowledgeScope(row["scope"]),project_id=row["project_id"],owner_user_id=row["owner_user_id"],media_type=row["media_type"],metadata=dict(row["metadata"] or {}),collection_id=str(row["collection_id"]) if row.get("collection_id") else None,created_at=row["created_at"],deleted_at=row["deleted_at"])
    async def list_documents(self,principal: KnowledgePrincipal,limit: int=100)->list[Document]:
        if limit<1 or limit>500:
            raise ValueError("limit must be between 1 and 500")
        conn=await self._connect()
        async with conn:
            cur=await conn.execute("""SELECT d.* FROM knowledge_documents d WHERE d.tenant_id=%s AND d.deleted_at IS NULL AND d.version=(SELECT max(v.version) FROM knowledge_documents v WHERE v.tenant_id=d.tenant_id AND v.document_id=d.document_id AND v.deleted_at IS NULL) AND ((d.scope='tenant') OR (d.scope='project' AND d.project_id=ANY(%s)) OR (d.scope='private' AND (d.owner_user_id=%s OR d.acl_enforced OR EXISTS(SELECT 1 FROM knowledge_document_acl ap WHERE ap.tenant_id=d.tenant_id AND ap.document_id=d.document_id)))) AND (d.collection_id IS NULL OR EXISTS(SELECT 1 FROM knowledge_collections kc WHERE kc.tenant_id=d.tenant_id AND kc.collection_id=d.collection_id AND kc.deleted_at IS NULL AND (kc.project_id IS NULL OR (d.scope='project' AND kc.project_id=d.project_id))))  AND (d.collection_id IS NULL OR EXISTS(SELECT 1 FROM knowledge_collections kc WHERE kc.tenant_id=d.tenant_id AND kc.collection_id=d.collection_id AND kc.deleted_at IS NULL AND (kc.project_id IS NULL OR (d.scope='project' AND kc.project_id=d.project_id)))) AND ((NOT d.acl_enforced AND NOT EXISTS(SELECT 1 FROM knowledge_document_acl ax WHERE ax.tenant_id=d.tenant_id AND ax.document_id=d.document_id)) OR (d.scope='private' AND d.owner_user_id=%s) OR EXISTS(SELECT 1 FROM knowledge_document_acl a WHERE a.tenant_id=d.tenant_id AND a.document_id=d.document_id AND a.permission IN ('read','manage') AND ((a.principal_type='user' AND a.principal_id=%s) OR (a.principal_type='role' AND a.principal_id=ANY(%s)) OR (a.principal_type='project' AND a.principal_id=ANY(%s))))) ORDER BY d.created_at DESC LIMIT %s""",(principal.tenant_id,list(principal.project_ids),principal.user_id,principal.user_id,principal.user_id,list(principal.roles),list(principal.project_ids),limit))
            rows=await cur.fetchall()
        return [Document(tenant_id=row["tenant_id"],document_id=str(row["document_id"]),version=row["version"],title=row["title"],source_uri=row["source_uri"],content_hash=row["content_hash"],scope=KnowledgeScope(row["scope"]),project_id=row["project_id"],owner_user_id=row["owner_user_id"],media_type=row["media_type"],metadata=dict(row["metadata"] or {}),collection_id=str(row["collection_id"]) if row.get("collection_id") else None,created_at=row["created_at"],deleted_at=row["deleted_at"]) for row in rows]
    @staticmethod
    def _hit(row: dict[str,Any],strategy: str)->RetrievalHit:
        return RetrievalHit(document_id=str(row["document_id"]),document_version=row["version"],chunk_id=str(row["chunk_id"]),text=row["body"],source_uri=row["source_uri"],title=row["title"],score=float(row["score"] or 0),page_number=row["page_number"],start_offset=row["start_offset"],end_offset=row["end_offset"],retrieval_trace={"strategy":strategy,"tenant_scoped":True,"acl_filtered_in_sql":True,"citation":{"document_id":str(row["document_id"]),"document_version":row["version"],"chunk_id":str(row["chunk_id"]),"source_uri":row["source_uri"],"title":row["title"],"page_number":row["page_number"],"start_offset":row["start_offset"],"end_offset":row["end_offset"]}})
    async def search(self,*,principal: KnowledgePrincipal,query: str,query_vector: Sequence[float]|None,limit: int)->list[RetrievalHit]:
        conn=await self._connect();roles=list(principal.roles);projects=list(principal.project_ids);candidate_limit=max(limit*4,20)
        acl="""((NOT d.acl_enforced AND NOT EXISTS(SELECT 1 FROM knowledge_document_acl ax WHERE ax.tenant_id=d.tenant_id AND ax.document_id=d.document_id)) OR (d.scope='private' AND d.owner_user_id=%s) OR EXISTS(SELECT 1 FROM knowledge_document_acl a WHERE a.tenant_id=d.tenant_id AND a.document_id=d.document_id AND a.permission IN ('read','manage') AND ((a.principal_type='user' AND a.principal_id=%s) OR (a.principal_type='role' AND a.principal_id=ANY(%s)) OR (a.principal_type='project' AND a.principal_id=ANY(%s)))))"""
        common="""d.tenant_id=%s AND d.deleted_at IS NULL AND d.version=(SELECT max(dv.version) FROM knowledge_documents dv WHERE dv.tenant_id=d.tenant_id AND dv.document_id=d.document_id AND dv.deleted_at IS NULL) AND ((d.scope='tenant') OR (d.scope='project' AND d.project_id=ANY(%s)) OR (d.scope='private' AND (d.owner_user_id=%s OR d.acl_enforced OR EXISTS(SELECT 1 FROM knowledge_document_acl ap WHERE ap.tenant_id=d.tenant_id AND ap.document_id=d.document_id)))) AND """+acl
        chunk_acl="""(NOT c.acl_enforced OR EXISTS(SELECT 1 FROM knowledge_chunk_acl ca WHERE ca.tenant_id=c.tenant_id AND ca.chunk_id=c.chunk_id AND ca.permission IN ('read','manage') AND ((ca.principal_type='user' AND ca.principal_id=%s) OR (ca.principal_type='role' AND ca.principal_id=ANY(%s)) OR (ca.principal_type='project' AND ca.principal_id=ANY(%s)))))"""
        lexical_sql="""SELECT d.document_id,d.version,d.title,d.source_uri,c.chunk_id,c.body,c.page_number,c.start_offset,c.end_offset,ts_rank_cd(c.search_vector,plainto_tsquery('simple',%s)) score FROM knowledge_documents d JOIN knowledge_chunks c ON c.tenant_id=d.tenant_id AND c.document_id=d.document_id AND c.document_version=d.version WHERE """+common+" AND "+chunk_acl+" AND c.search_vector @@ plainto_tsquery('simple',%s) ORDER BY score DESC LIMIT %s"
        lexical_params=(query,principal.tenant_id,projects,principal.user_id,principal.user_id,principal.user_id,roles,projects,principal.user_id,roles,projects,query,candidate_limit)
        async with conn:
            # search() 只负责候选召回；词法/语义的选择由上层 retrieval 策略决定。
            cur=await conn.execute(lexical_sql,lexical_params);lexical=await cur.fetchall()
            semantic=[]
            if query_vector:
                literal="["+",".join(str(float(x)) for x in query_vector)+"]"
                semantic_sql="""SELECT d.document_id,d.version,d.title,d.source_uri,c.chunk_id,c.body,c.page_number,c.start_offset,c.end_offset,(1-(e.embedding <=> %s::vector)) score FROM knowledge_documents d JOIN knowledge_chunks c ON c.tenant_id=d.tenant_id AND c.document_id=d.document_id AND c.document_version=d.version JOIN knowledge_embeddings e ON e.tenant_id=c.tenant_id AND e.chunk_id=c.chunk_id AND e.embedding_model=%s WHERE """+common+" AND "+chunk_acl+" ORDER BY e.embedding <=> %s::vector LIMIT %s"
                semantic_params=(literal,self.embedding_model,principal.tenant_id,projects,principal.user_id,principal.user_id,principal.user_id,roles,projects,principal.user_id,roles,projects,literal,candidate_limit)
                cur=await conn.execute(semantic_sql,semantic_params);semantic=await cur.fetchall()
        # Reciprocal-rank fusion combines independent lexical and semantic candidate lists.
        fused:dict[str,tuple[float,dict[str,Any],set[str]]]={}
        for kind,rows in (("postgres-fts",lexical),("pgvector",semantic)):
            for rank,row in enumerate(rows,1):
                key=str(row["chunk_id"]);score=1.0/(60+rank)
                if key not in fused:fused[key]=(score,row,{kind})
                else:
                    prior,prior_row,kinds=fused[key];kinds.add(kind);fused[key]=(prior+score,prior_row,kinds)
        ordered=sorted(fused.values(),key=lambda x:x[0],reverse=True)[:limit]
        hits=[]
        for score,row,kinds in ordered:
            enriched=dict(row);enriched["score"]=score;hits.append(self._hit(enriched,"hybrid:"+ "+".join(sorted(kinds))))
        return hits
    async def get_connector_cursor(self,tenant_id: str,connector_id: str)->str|None:
        conn=await self._connect()
        async with conn:
            cur=await conn.execute("SELECT cursor FROM knowledge_connector_sync_state WHERE tenant_id=%s AND connector_id=%s",(tenant_id,connector_id))
            row=await cur.fetchone()
        return row["cursor"] if row else None
    async def save_connector_cursor(self,tenant_id: str,connector_id: str,cursor: str)->None:
        conn=await self._connect()
        async with conn:
            async with conn.transaction():
                await conn.execute("""INSERT INTO knowledge_connector_sync_state(tenant_id,connector_id,cursor,last_synced_at) VALUES(%s,%s,%s,now()) ON CONFLICT(tenant_id,connector_id) DO UPDATE SET cursor=EXCLUDED.cursor,last_synced_at=now(),last_error=NULL""",(tenant_id,connector_id,cursor))
    async def grant_document_access(
        self, tenant_id: str, document_id: str, principal_type: str,
        principal_id: str, permission: str, granted_by: str,
    ) -> None:
        if principal_type not in {"user", "role", "project"}:
            raise ValueError("invalid ACL principal type")
        if permission not in {"read", "manage"}:
            raise ValueError("invalid ACL permission")
        if not principal_id.strip() or not granted_by.strip():
            raise ValueError("ACL principal and grantor are required")
        conn = await self._connect()
        async with conn:
            async with conn.transaction():
                cur = await conn.execute(
                    "SELECT 1 FROM knowledge_documents WHERE tenant_id=%s AND document_id=%s AND deleted_at IS NULL ORDER BY version DESC LIMIT 1",
                    (tenant_id, document_id),
                )
                if await cur.fetchone() is None:
                    raise ValueError("document not found")
                await conn.execute(
                    "UPDATE knowledge_documents SET acl_enforced=true WHERE tenant_id=%s AND document_id=%s AND deleted_at IS NULL",
                    (tenant_id, document_id),
                )
                await conn.execute(
                    "INSERT INTO knowledge_cache_invalidations(tenant_id,resource_type,resource_id,reason) VALUES(%s,'document',%s,'acl_changed')",
                    (tenant_id, document_id),
                )
                await conn.execute(
                    """INSERT INTO knowledge_document_acl
                    (tenant_id,document_id,principal_type,principal_id,permission,granted_by)
                    VALUES(%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(tenant_id,document_id,principal_type,principal_id,permission)
                    DO UPDATE SET granted_by=EXCLUDED.granted_by,created_at=now()""",
                    (tenant_id, document_id, principal_type, principal_id, permission, granted_by),
                )

    async def revoke_document_access(
        self, tenant_id: str, document_id: str, principal_type: str,
        principal_id: str, permission: str,
    ) -> bool:
        conn = await self._connect()
        async with conn:
            async with conn.transaction():
                cur = await conn.execute(
                    """DELETE FROM knowledge_document_acl
                    WHERE tenant_id=%s AND document_id=%s AND principal_type=%s
                    AND principal_id=%s AND permission=%s""",
                    (tenant_id, document_id, principal_type, principal_id, permission),
                )
                if cur.rowcount:
                    await conn.execute(
                        "INSERT INTO knowledge_cache_invalidations(tenant_id,resource_type,resource_id,reason) VALUES(%s,'document',%s,'acl_changed')",
                        (tenant_id, document_id),
                    )
                return cur.rowcount > 0

    async def replace_document_access(
        self, tenant_id: str, document_id: str,
        grants: Sequence[tuple[str, str, str]], granted_by: str,
    ) -> None:
        if not granted_by.strip():
            raise ValueError("ACL grantor is required")
        normalized = set(grants)
        if any(
            kind not in {"user", "role", "project"}
            or permission not in {"read", "manage"}
            or not identifier.strip()
            for kind, identifier, permission in normalized
        ):
            raise ValueError("invalid connector ACL grant")
        conn = await self._connect()
        async with conn:
            async with conn.transaction():
                cur = await conn.execute(
                    "SELECT 1 FROM knowledge_documents WHERE tenant_id=%s AND document_id=%s AND deleted_at IS NULL ORDER BY version DESC LIMIT 1",
                    (tenant_id, document_id),
                )
                if await cur.fetchone() is None:
                    raise ValueError("document not found")
                # Enforce ACL before replacing grants. An empty grant set means
                # source-side sharing is private to the mapped owner only.
                await conn.execute(
                    "UPDATE knowledge_documents SET acl_enforced=true WHERE tenant_id=%s AND document_id=%s AND deleted_at IS NULL",
                    (tenant_id, document_id),
                )
                await conn.execute(
                    "DELETE FROM knowledge_document_acl WHERE tenant_id=%s AND document_id=%s",
                    (tenant_id, document_id),
                )
                await conn.execute(
                    "DELETE FROM knowledge_chunk_acl WHERE tenant_id=%s AND chunk_id IN (SELECT chunk_id FROM knowledge_chunks WHERE tenant_id=%s AND document_id=%s)",
                    (tenant_id, tenant_id, document_id),
                )
                await conn.execute(
                    "INSERT INTO knowledge_cache_invalidations(tenant_id,resource_type,resource_id,reason) VALUES(%s,'document',%s,'acl_changed')",
                    (tenant_id, document_id),
                )
                for kind, identifier, permission in sorted(normalized):
                    await conn.execute(
                        """INSERT INTO knowledge_document_acl
                        (tenant_id,document_id,principal_type,principal_id,permission,granted_by)
                        VALUES(%s,%s,%s,%s,%s,%s)""",
                        (tenant_id, document_id, kind, identifier, permission, granted_by),
                    )

    async def grant_chunk_access(self, tenant_id: str, document_id: str, chunk_id: str, principal_type: str, principal_id: str, permission: str, granted_by: str) -> None:
        if principal_type not in {"user", "role", "project"} or permission not in {"read", "manage"}:
            raise ValueError("invalid chunk ACL")
        if not principal_id.strip() or not granted_by.strip():
            raise ValueError("chunk ACL principal and grantor are required")
        conn = await self._connect()
        async with conn:
            async with conn.transaction():
                cur = await conn.execute("SELECT 1 FROM knowledge_chunks WHERE tenant_id=%s AND document_id=%s AND chunk_id=%s", (tenant_id, document_id, chunk_id))
                if await cur.fetchone() is None:
                    raise ValueError("chunk not found")
                await conn.execute("UPDATE knowledge_chunks SET acl_enforced=true WHERE tenant_id=%s AND chunk_id=%s", (tenant_id, chunk_id))
                await conn.execute("""INSERT INTO knowledge_chunk_acl (tenant_id,chunk_id,principal_type,principal_id,permission,granted_by) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,chunk_id,principal_type,principal_id,permission) DO UPDATE SET granted_by=EXCLUDED.granted_by,created_at=now()""", (tenant_id, chunk_id, principal_type, principal_id, permission, granted_by))
                await conn.execute("INSERT INTO knowledge_cache_invalidations(tenant_id,resource_type,resource_id,reason) VALUES(%s,'chunk',%s,'acl_changed')", (tenant_id, chunk_id))

    async def revoke_chunk_access(self, tenant_id: str, chunk_id: str, principal_type: str, principal_id: str, permission: str) -> bool:
        conn = await self._connect()
        async with conn:
            async with conn.transaction():
                cur = await conn.execute("DELETE FROM knowledge_chunk_acl WHERE tenant_id=%s AND chunk_id=%s AND principal_type=%s AND principal_id=%s AND permission=%s", (tenant_id, chunk_id, principal_type, principal_id, permission))
                if cur.rowcount:
                    await conn.execute("INSERT INTO knowledge_cache_invalidations(tenant_id,resource_type,resource_id,reason) VALUES(%s,'chunk',%s,'acl_changed')", (tenant_id, chunk_id))
                return cur.rowcount > 0

    async def replace_document_access(
        self, tenant_id: str, document_id: str,
        grants: Sequence[tuple[str, str, str]], granted_by: str,
    ) -> None:
        if not granted_by.strip():
            raise ValueError("ACL grantor is required")
        normalized = set(grants)
        if any(
            kind not in {"user", "role", "project"}
            or permission not in {"read", "manage"}
            or not identifier.strip()
            for kind, identifier, permission in normalized
        ):
            raise ValueError("invalid connector ACL grant")
        conn = await self._connect()
        async with conn:
            async with conn.transaction():
                cur = await conn.execute(
                    "SELECT 1 FROM knowledge_documents WHERE tenant_id=%s AND document_id=%s AND deleted_at IS NULL ORDER BY version DESC LIMIT 1",
                    (tenant_id, document_id),
                )
                if await cur.fetchone() is None:
                    raise ValueError("document not found")
                # Enforce ACL before replacing grants. An empty grant set means
                # source-side sharing is private to the mapped owner only.
                await conn.execute(
                    "UPDATE knowledge_documents SET acl_enforced=true WHERE tenant_id=%s AND document_id=%s AND deleted_at IS NULL",
                    (tenant_id, document_id),
                )
                await conn.execute(
                    "DELETE FROM knowledge_document_acl WHERE tenant_id=%s AND document_id=%s",
                    (tenant_id, document_id),
                )
                for kind, identifier, permission in sorted(normalized):
                    await conn.execute(
                        """INSERT INTO knowledge_document_acl
                        (tenant_id,document_id,principal_type,principal_id,permission,granted_by)
                        VALUES(%s,%s,%s,%s,%s,%s)""",
                        (tenant_id, document_id, kind, identifier, permission, granted_by),
                    )

    async def list_chunks(self, tenant_id: str, document_id: str, version: int) -> list[DocumentChunk]:
        conn = await self._connect()
        async with conn:
            cur = await conn.execute("SELECT * FROM knowledge_chunks WHERE tenant_id=%s AND document_id=%s AND document_version=%s ORDER BY ordinal", (tenant_id, document_id, version))
            rows = await cur.fetchall()
        return [DocumentChunk(tenant_id=r["tenant_id"], document_id=str(r["document_id"]), document_version=r["document_version"], ordinal=r["ordinal"], text=r["body"], content_hash=r["content_hash"], start_offset=r["start_offset"], end_offset=r["end_offset"], page_number=r["page_number"], chunk_id=str(r["chunk_id"]), metadata=dict(r["metadata"] or {})) for r in rows]
    
    async def list_visible_chunks(
        self, principal: KnowledgePrincipal, document_id: str, version: int,
    ) -> list[DocumentChunk]:
        """Return only chunks authorized for this principal before exposing bodies."""
        conn = await self._connect()
        async with conn:
            cur = await conn.execute(
                """SELECT c.* FROM knowledge_chunks c
                WHERE c.tenant_id=%s AND c.document_id=%s AND c.document_version=%s
                AND (NOT c.acl_enforced OR EXISTS (
                    SELECT 1 FROM knowledge_chunk_acl a
                    WHERE a.tenant_id=c.tenant_id AND a.chunk_id=c.chunk_id
                    AND a.permission IN ('read','manage')
                    AND ((a.principal_type='user' AND a.principal_id=%s)
                      OR (a.principal_type='role' AND a.principal_id=ANY(%s))
                      OR (a.principal_type='project' AND a.principal_id=ANY(%s)))
                ))
                ORDER BY c.ordinal""",
                (principal.tenant_id, document_id, version, principal.user_id,
                 list(principal.roles), list(principal.project_ids)),
            )
            rows = await cur.fetchall()
        return [DocumentChunk(
            tenant_id=row["tenant_id"], document_id=str(row["document_id"]),
            document_version=row["document_version"], ordinal=row["ordinal"],
            text=row["body"], content_hash=row["content_hash"],
            start_offset=row["start_offset"], end_offset=row["end_offset"],
            page_number=row["page_number"], chunk_id=str(row["chunk_id"]),
            metadata=dict(row["metadata"] or {}),
        ) for row in rows]

    async def update_chunk(self, tenant_id: str, document_id: str, version: int, chunk_id: str, text: str) -> DocumentChunk | None:
        if not isinstance(text, str) or not text.strip() or len(text) > 20000:
            raise ValueError("chunk text must contain 1-20000 characters")
        from agent_platform.knowledge.chunking import sha256_text
        conn = await self._connect()
        async with conn:
            async with conn.transaction():
                cur = await conn.execute("UPDATE knowledge_chunks SET body=%s, content_hash=%s, end_offset=start_offset+char_length(%s), metadata=metadata || %s WHERE tenant_id=%s AND document_id=%s AND document_version=%s AND chunk_id=%s RETURNING *", (text, sha256_text(text), text, _jsonb({"embedding_status":"stale"}), tenant_id, document_id, version, chunk_id))
                row = await cur.fetchone()
                if row is None: return None
                await conn.execute("DELETE FROM knowledge_embeddings WHERE tenant_id=%s AND chunk_id=%s", (tenant_id, chunk_id))
                await conn.execute("INSERT INTO knowledge_cache_invalidations(tenant_id,resource_type,resource_id,reason) VALUES(%s,'chunk',%s,'updated')", (tenant_id, chunk_id))
        return DocumentChunk(tenant_id=row["tenant_id"], document_id=str(row["document_id"]), document_version=row["document_version"], ordinal=row["ordinal"], text=row["body"], content_hash=row["content_hash"], start_offset=row["start_offset"], end_offset=row["end_offset"], page_number=row["page_number"], chunk_id=str(row["chunk_id"]), metadata=dict(row["metadata"] or {}))

    async def delete_document(self,tenant_id: str,document_id: str)->bool:
        conn=await self._connect()
        async with conn:
            async with conn.transaction():
                cur=await conn.execute("UPDATE knowledge_documents SET deleted_at=now() WHERE tenant_id=%s AND document_id=%s AND deleted_at IS NULL",(tenant_id,document_id))
                if cur.rowcount<1:return False
                await conn.execute(
                    "INSERT INTO knowledge_cache_invalidations(tenant_id,resource_type,resource_id,reason) VALUES(%s,'document',%s,'deleted')",
                    (tenant_id, document_id),
                )
                await conn.execute(
                    """INSERT INTO knowledge_cache_invalidations(tenant_id,resource_type,resource_id,reason)
                    SELECT tenant_id,'chunk',chunk_id,'deleted' FROM knowledge_chunks
                    WHERE tenant_id=%s AND document_id=%s""",
                    (tenant_id, document_id),
                )
                # Remove derived vectors in the same transaction as source chunks.
                await conn.execute("DELETE FROM knowledge_embeddings WHERE tenant_id=%s AND chunk_id IN (SELECT chunk_id FROM knowledge_chunks WHERE tenant_id=%s AND document_id=%s)",(tenant_id,tenant_id,document_id))
                await conn.execute("DELETE FROM knowledge_chunks WHERE tenant_id=%s AND document_id=%s",(tenant_id,document_id))
                await conn.execute("DELETE FROM knowledge_document_acl WHERE tenant_id=%s AND document_id=%s",(tenant_id,document_id))
                return True
