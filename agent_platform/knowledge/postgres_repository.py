"""PostgreSQL repository with tenant/ACL-first hybrid retrieval."""
from __future__ import annotations
from typing import Any,Callable,Sequence
from agent_platform.knowledge.contracts import Document,DocumentChunk,KnowledgePrincipal,KnowledgeScope,RetrievalHit
ConnectionFactory=Callable[[],Any]
def _jsonb(value: Any)->Any:
    try:from psycopg.types.json import Jsonb
    except ImportError as exc:raise RuntimeError("Install psycopg[binary,pool]") from exc
    return Jsonb(value)
class PostgresKnowledgeRepository:
    def __init__(self,connection_factory: ConnectionFactory,*,embedding_model: str="default")->None:self._connect=connection_factory;self.embedding_model=embedding_model
    async def save_document(self,document: Document,chunks: Sequence[DocumentChunk])->bool:
        conn=await self._connect()
        async with conn:
            async with conn.transaction():
                cur=await conn.execute("SELECT 1 FROM knowledge_documents WHERE tenant_id=%s AND document_id=%s AND version=%s",(document.tenant_id,document.document_id,document.version))
                if await cur.fetchone():return False
                await conn.execute("""INSERT INTO knowledge_documents(tenant_id,document_id,version,title,source_uri,content_hash,scope,project_id,owner_user_id,media_type,metadata) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",(document.tenant_id,document.document_id,document.version,document.title,document.source_uri,document.content_hash,document.scope.value,document.project_id,document.owner_user_id,document.media_type,_jsonb(document.metadata)))
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
                    await conn.execute("""INSERT INTO knowledge_embeddings(tenant_id,chunk_id,embedding_model,embedding) VALUES(%s,%s,%s,%s::vector) ON CONFLICT(tenant_id,chunk_id,embedding_model) DO UPDATE SET embedding=EXCLUDED.embedding,created_at=now()""",(tenant_id,chunk.chunk_id,self.embedding_model,literal))
    async def get_document(self,tenant_id: str,document_id: str,version: int|None=None)->Document|None:
        conn=await self._connect()
        async with conn:
            sql="SELECT * FROM knowledge_documents WHERE tenant_id=%s AND document_id=%s AND deleted_at IS NULL";params:list[Any]=[tenant_id,document_id]
            if version is not None:sql+=" AND version=%s";params.append(version)
            sql+=" ORDER BY version DESC LIMIT 1";cur=await conn.execute(sql,tuple(params));row=await cur.fetchone()
        if not row:return None
        return Document(tenant_id=row["tenant_id"],document_id=str(row["document_id"]),version=row["version"],title=row["title"],source_uri=row["source_uri"],content_hash=row["content_hash"],scope=KnowledgeScope(row["scope"]),project_id=row["project_id"],owner_user_id=row["owner_user_id"],media_type=row["media_type"],metadata=dict(row["metadata"] or {}),created_at=row["created_at"],deleted_at=row["deleted_at"])
    async def list_documents(self,principal: KnowledgePrincipal,limit: int=100)->list[Document]:
        if limit<1 or limit>500:
            raise ValueError("limit must be between 1 and 500")
        conn=await self._connect()
        async with conn:
            cur=await conn.execute("""SELECT d.* FROM knowledge_documents d WHERE d.tenant_id=%s AND d.deleted_at IS NULL AND d.version=(SELECT max(v.version) FROM knowledge_documents v WHERE v.tenant_id=d.tenant_id AND v.document_id=d.document_id AND v.deleted_at IS NULL) AND ((d.scope='tenant') OR (d.scope='project' AND d.project_id=ANY(%s)) OR (d.scope='private' AND d.owner_user_id=%s)) AND (NOT EXISTS(SELECT 1 FROM knowledge_document_acl ax WHERE ax.tenant_id=d.tenant_id AND ax.document_id=d.document_id) OR EXISTS(SELECT 1 FROM knowledge_document_acl a WHERE a.tenant_id=d.tenant_id AND a.document_id=d.document_id AND a.permission='read' AND ((a.principal_type='user' AND a.principal_id=%s) OR (a.principal_type='role' AND a.principal_id=ANY(%s)) OR (a.principal_type='project' AND a.principal_id=ANY(%s)))) ORDER BY d.created_at DESC LIMIT %s""",(principal.tenant_id,list(principal.project_ids),principal.user_id,principal.user_id,list(principal.roles),list(principal.project_ids),limit))
            rows=await cur.fetchall()
        return [Document(tenant_id=row["tenant_id"],document_id=str(row["document_id"]),version=row["version"],title=row["title"],source_uri=row["source_uri"],content_hash=row["content_hash"],scope=KnowledgeScope(row["scope"]),project_id=row["project_id"],owner_user_id=row["owner_user_id"],media_type=row["media_type"],metadata=dict(row["metadata"] or {}),created_at=row["created_at"],deleted_at=row["deleted_at"]) for row in rows]
    @staticmethod
    def _hit(row: dict[str,Any],strategy: str)->RetrievalHit:
        return RetrievalHit(document_id=str(row["document_id"]),document_version=row["version"],chunk_id=str(row["chunk_id"]),text=row["body"],source_uri=row["source_uri"],title=row["title"],score=float(row["score"] or 0),page_number=row["page_number"],start_offset=row["start_offset"],end_offset=row["end_offset"],retrieval_trace={"strategy":strategy,"tenant_scoped":True,"acl_filtered_in_sql":True})
    async def search(self,*,principal: KnowledgePrincipal,query: str,query_vector: Sequence[float]|None,limit: int)->list[RetrievalHit]:
        conn=await self._connect();roles=list(principal.roles);projects=list(principal.project_ids);candidate_limit=max(limit*4,20)
        acl="""(NOT EXISTS(SELECT 1 FROM knowledge_document_acl ax WHERE ax.tenant_id=d.tenant_id AND ax.document_id=d.document_id) OR EXISTS(SELECT 1 FROM knowledge_document_acl a WHERE a.tenant_id=d.tenant_id AND a.document_id=d.document_id AND a.permission='read' AND ((a.principal_type='user' AND a.principal_id=%s) OR (a.principal_type='role' AND a.principal_id=ANY(%s)) OR (a.principal_type='project' AND a.principal_id=ANY(%s))))"""
        common="""d.tenant_id=%s AND d.deleted_at IS NULL AND d.version=(SELECT max(dv.version) FROM knowledge_documents dv WHERE dv.tenant_id=d.tenant_id AND dv.document_id=d.document_id AND dv.deleted_at IS NULL) AND ((d.scope='tenant') OR (d.scope='project' AND d.project_id=ANY(%s)) OR (d.scope='private' AND d.owner_user_id=%s)) AND """+acl
        lexical_sql="""SELECT d.document_id,d.version,d.title,d.source_uri,c.chunk_id,c.body,c.page_number,c.start_offset,c.end_offset,ts_rank_cd(c.search_vector,plainto_tsquery('simple',%s)) score FROM knowledge_documents d JOIN knowledge_chunks c ON c.tenant_id=d.tenant_id AND c.document_id=d.document_id AND c.document_version=d.version WHERE """+common+""" AND c.search_vector @@ plainto_tsquery('simple',%s) ORDER BY score DESC LIMIT %s"""
        lexical_params=(query,principal.tenant_id,projects,principal.user_id,principal.user_id,roles,projects,query,candidate_limit)
        async with conn:
            cur=await conn.execute(lexical_sql,lexical_params);lexical=await cur.fetchall()
            semantic=[]
            if query_vector:
                literal="["+",".join(str(float(x)) for x in query_vector)+"]"
                semantic_sql="""SELECT d.document_id,d.version,d.title,d.source_uri,c.chunk_id,c.body,c.page_number,c.start_offset,c.end_offset,(1-(e.embedding <=> %s::vector)) score FROM knowledge_documents d JOIN knowledge_chunks c ON c.tenant_id=d.tenant_id AND c.document_id=d.document_id AND c.document_version=d.version JOIN knowledge_embeddings e ON e.tenant_id=c.tenant_id AND e.chunk_id=c.chunk_id AND e.embedding_model=%s WHERE """+common+""" ORDER BY e.embedding <=> %s::vector LIMIT %s"""
                semantic_params=(literal,self.embedding_model,principal.tenant_id,projects,principal.user_id,principal.user_id,roles,projects,literal,candidate_limit)
                cur=await conn.execute(semantic_sql,semantic_params);semantic=await cur.fetchall()
        # Reciprocal-rank fusion combines independent lexical and semantic candidate lists.
        fused:dict[str,tuple[float,dict[str,Any],set[str]]]={}
        for strategy,rows in (("postgres-fts",lexical),("pgvector",semantic)):
            for rank,row in enumerate(rows,1):
                key=str(row["chunk_id"]);score=1.0/(60+rank)
                if key not in fused:fused[key]=(score,row,{strategy})
                else:
                    prior,prior_row,kinds=fused[key];kinds.add(strategy);fused[key]=(prior+score,prior_row,kinds)
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
    async def delete_document(self,tenant_id: str,document_id: str)->bool:
        conn=await self._connect()
        async with conn:
            async with conn.transaction():
                cur=await conn.execute("UPDATE knowledge_documents SET deleted_at=now() WHERE tenant_id=%s AND document_id=%s AND deleted_at IS NULL",(tenant_id,document_id))
                if cur.rowcount!=1:return False
                await conn.execute("DELETE FROM knowledge_chunks WHERE tenant_id=%s AND document_id=%s",(tenant_id,document_id))
                await conn.execute("DELETE FROM knowledge_document_acl WHERE tenant_id=%s AND document_id=%s",(tenant_id,document_id))
                return True
