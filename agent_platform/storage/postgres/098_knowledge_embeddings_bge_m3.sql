-- 本地 bge-m3 向量维度为 1024；原表固定 vector(1536) 且 HNSW 索引依赖维度。
-- 向量是可重新计算的派生数据，这里清空后按 1024 维重建列与余弦索引。
BEGIN;

DROP INDEX IF EXISTS idx_knowledge_embeddings_cosine;

DELETE FROM knowledge_embeddings;

ALTER TABLE knowledge_embeddings
    ALTER COLUMN embedding TYPE vector(1024) USING embedding::vector(1024);

ALTER TABLE knowledge_embeddings
    ALTER COLUMN dimensions SET DEFAULT 1024;

CREATE INDEX idx_knowledge_embeddings_cosine
    ON knowledge_embeddings USING hnsw (embedding vector_cosine_ops);

COMMIT;
