#!/usr/bin/env python3
"""vectors.py — 嵌入与向量检索

设计依据：08《语义与记忆设计》§3、09《分项计划》T-0010。

接口（上层只依赖这几个函数，换实现不改调用方）：
    index_path(path, project, source_type, source_id, provider)
    index_text(text, project, source_type, source_id, provider)   运行时文本（关单经验）
    search(query, project, k, provider, source_types)
    delete_by_source(source_type, source_id)

主路径：Ollama bge-m3（1024 维，L2 归一化）。
降级：hash-tfidf（512 桶稀疏 tf，查询时按语料计算 idf），Ollama 不可用时自动切换。
"""

import array
import json
import math
import os
import re
import urllib.error
import urllib.request
import zlib
from collections import Counter
from contextlib import closing
from pathlib import Path

import db as dbm

ROOT = Path(__file__).resolve().parent
CHUNK_SIZE = 512
OVERLAP = 64
HASH_MODEL = "hash-tfidf-512"
HASH_DIM = 512
OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
EMBED_MODEL = os.environ.get("AGENCY_EMBED_MODEL", "bge-m3")


class VectorError(Exception):
    pass


# ---------- 切块与词元 ----------

_WORD = re.compile(r"[a-z0-9_]+")
_CJK = re.compile(r"[\u4e00-\u9fff]")


def tokenize(text):
    low = text.lower()
    words = _WORD.findall(low)
    cjk = _CJK.findall(low)
    return words + cjk + [cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)]


def tf(text):
    """哈希桶 → 词频。稀疏存储，避免全量向量。"""
    counts = Counter(zlib.crc32(t.encode("utf-8")) % HASH_DIM for t in tokenize(text))
    return {str(b): n for b, n in counts.items()}


def _chunk_long(paragraph, size, overlap):
    out, start = [], 0
    while start < len(paragraph):
        end = min(start + size, len(paragraph))
        if end < len(paragraph):
            cut = max(paragraph.rfind(x, start + size // 2, end) for x in "。！？；\n")
            if cut > start:
                end = cut + 1
        out.append(paragraph[start:end].strip())
        if end >= len(paragraph):
            break
        start = max(end - overlap, start + 1)
    return out


def chunk_text(text, size=CHUNK_SIZE, overlap=OVERLAP):
    """按段落边界切块，超长段硬切并保留 overlap。"""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, buf = [], ""
    for p in paragraphs:
        if len(p) > size:
            if buf.strip():
                chunks.append(buf.strip())
                buf = ""
            chunks.extend(_chunk_long(p, size, overlap))
        elif len(buf) + len(p) + 1 <= size:
            buf = (buf + "\n" + p).strip() if buf else p
        else:
            if buf.strip():
                chunks.append(buf.strip())
            buf = p
    if buf.strip():
        chunks.append(buf.strip())
    return chunks


# ---------- 向量编解码 ----------

def _normalize(vec):
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def _pack_dense(vec):
    return array.array("f", vec).tobytes()


def _unpack_dense(blob):
    arr = array.array("f")
    arr.frombytes(blob)
    return arr


def _sparse_weights(counts, idf):
    return {int(b): (1.0 + math.log(n)) * idf.get(int(b), 1.0) for b, n in counts.items()}


def _cosine_sparse(a, b):
    if not a or not b:
        return 0.0
    dot = sum(w * b[bucket] for bucket, w in a.items() if bucket in b)
    na = math.sqrt(sum(w * w for w in a.values()))
    nb = math.sqrt(sum(w * w for w in b.values()))
    return dot / (na * nb) if na and nb else 0.0


# ---------- 嵌入后端 ----------

def ollama_available():
    try:
        with urllib.request.urlopen(OLLAMA_URL + "/api/tags", timeout=2):
            return True
    except Exception:  # noqa: BLE001
        return False


def _ollama_embed(texts, timeout=120):
    body = json.dumps({"model": EMBED_MODEL, "input": texts}).encode()
    req = urllib.request.Request(
        OLLAMA_URL + "/api/embed", data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        embs = data.get("embeddings") or []
        if embs:
            return embs
    except urllib.error.HTTPError:
        pass  # 旧版 Ollama 没有 /api/embed，退回逐条端点
    out = []
    for t in texts:
        req = urllib.request.Request(
            OLLAMA_URL + "/api/embeddings",
            data=json.dumps({"model": EMBED_MODEL, "prompt": t}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            out.append(json.loads(resp.read())["embedding"])
    return out


def pick_provider(provider=None):
    forced = provider or os.environ.get("AGENCY_EMBED_PROVIDER")
    if forced in ("ollama", "hash"):
        return forced
    return "ollama" if ollama_available() else "hash"


# ---------- 索引 ----------

def _connect():
    con = dbm.connect(dbm.DB_PATH)
    dbm.migrate(con, verbose=False)
    return con


def _iter_files(path):
    p = Path(path).expanduser().resolve()
    if p.is_dir():
        return sorted(x for x in list(p.rglob("*.md")) + list(p.rglob("*.txt")) if x.is_file())
    if p.is_file():
        return [p]
    raise VectorError("路径不存在：" + str(path))


def _source_id(p):
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def index_path(path, project=None, source_type="doc", source_id=None, provider=None):
    """索引单文件或目录。同一内容不重复嵌入；换模型时旧模型向量标 stale。"""
    clear_matrix_cache()
    total = {"added": 0, "skipped": 0, "files": 0, "model": "", "dim": 0,
             "degraded": False, "source_ids": []}
    mode = pick_provider(provider)
    files = _iter_files(path)
    for p in files:
        if p.suffix.lower() not in (".md", ".txt"):
            continue
        result = _index_file(p, project, source_type,
                             source_id if len(files) == 1 else None, mode)
        total["files"] += 1
        total["added"] += result["added"]
        total["skipped"] += result["skipped"]
        total["model"] = result["model"]
        total["dim"] = result["dim"]
        total["degraded"] = total["degraded"] or result["degraded"]
        total["source_ids"].append(result["source_id"])
    if not total["files"]:
        raise VectorError("没有可索引的 .md/.txt 文件")
    return total


GLOBAL_DIRS = ("company", "templates")


def _detach_project(sid):
    """把已按项目索引的同一文件改为全局：先删旧行，否则按项目过滤后会查不到。"""
    with closing(_connect()) as con:
        con.execute(
            "DELETE FROM embeddings WHERE source_type = 'doc' AND source_id = ?"
            " AND project_id IS NOT NULL", (sid,))
        con.commit()


def index_all(provider=None):
    """全量索引公司级资产（company/ 与 templates/）：project_id 为空，所有项目共用。"""
    total = {"added": 0, "skipped": 0, "files": 0, "model": "", "dim": 0,
             "degraded": False, "source_ids": []}
    mode = pick_provider(provider)
    for name in GLOBAL_DIRS:
        root = ROOT / name
        if not root.is_dir():
            continue
        for p in _iter_files(root):
            if "受限资料" in p.parts:  # 宪法：敏感信息不进共享检索
                continue
            _detach_project(_source_id(p))
            result = _index_file(p, None, "doc", None, mode)
            total["files"] += 1
            total["added"] += result["added"]
            total["skipped"] += result["skipped"]
            total["model"] = result["model"]
            total["dim"] = result["dim"]
            total["degraded"] = total["degraded"] or result["degraded"]
            total["source_ids"].append(result["source_id"])
    if not total["files"]:
        raise VectorError("没有可索引的 .md/.txt 文件")
    return total


def _index_file(path, project, source_type, source_id, mode):
    text = path.read_text(encoding="utf-8", errors="ignore")
    chunks = chunk_text(text)
    if not chunks:
        raise VectorError("文件为空：" + str(path))
    sid = source_id or _source_id(path)
    return _index_chunks(chunks, dbm.content_hash(text), project, source_type, sid, mode)


def index_text(text, project=None, source_type="case", source_id=None, provider=None):
    """把运行时生成的一段文本写入索引（关单经验等非文件内容）。

    source_id 必须显式给，便于按来源删除/覆盖；同一 source_id 重写走同一套去重逻辑。
    """
    text = (text or "").strip()
    if not text:
        raise VectorError("文本为空，无可索引内容")
    if not source_id:
        raise VectorError("index_text 必须显式指定 source_id")
    chunks = chunk_text(text)
    if not chunks:
        raise VectorError("文本切块后为空")
    return _index_chunks(chunks, dbm.content_hash(text), project, source_type,
                         source_id, pick_provider(provider))


def _index_chunks(chunks, version, project, source_type, sid, mode):
    model, dim, degraded = (EMBED_MODEL, 0, False) if mode == "ollama" else (HASH_MODEL, HASH_DIM, False)
    with closing(_connect()) as con:
        existing = {
            r["content_hash"]: r for r in con.execute(
                "SELECT chunk_no, content_hash, vector, dim FROM embeddings"
                " WHERE source_type = ? AND source_id = ? AND model = ?",
                (source_type, sid, model),
            )
        }
        pending = [(i, c) for i, c in enumerate(chunks)
                   if dbm.content_hash(c) not in existing]
        if not pending:  # 全量命中，维度沿用库内记录
            dim = next((r["dim"] for r in existing.values() if r["dim"]),
                       1024 if model == EMBED_MODEL else HASH_DIM)
        new_vectors = {}
        if pending:
            texts = [c for _, c in pending]
            try:
                if mode == "ollama":
                    vectors = _ollama_embed(texts)
                    dim = len(vectors[0])
                    new_vectors = {i: _pack_dense(_normalize(v))
                                   for (i, _), v in zip(pending, vectors)}
                else:
                    new_vectors = {i: json.dumps(tf(c)).encode()
                                   for i, c in pending}
            except (urllib.error.URLError, TimeoutError, OSError, KeyError) as e:
                if mode != "ollama":
                    raise VectorError("嵌入失败：" + str(e))
                # Ollama 掉线：本次整体降级为 hash，保证可检索
                model, dim, degraded = HASH_MODEL, HASH_DIM, True
                existing = {
                    r["content_hash"]: r for r in con.execute(
                        "SELECT chunk_no, content_hash, vector, dim FROM embeddings"
                        " WHERE source_type = ? AND source_id = ? AND model = ?",
                        (source_type, sid, model),
                    )
                }
                pending = [(i, c) for i, c in enumerate(chunks)
                           if dbm.content_hash(c) not in existing]
                new_vectors = {i: json.dumps(tf(c)).encode() for i, c in pending}

        for i, chunk in enumerate(chunks):
            digest = dbm.content_hash(chunk)
            row = existing.get(digest)
            vector = row["vector"] if row else new_vectors[i]
            chunk_dim = row["dim"] if row else dim
            con.execute(
                "INSERT OR REPLACE INTO embeddings(project_id, source_type, source_id,"
                " source_version, chunk_no, text, content_hash, model, dim, vector,"
                " state, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)",
                (project or None, source_type, sid, version, i, chunk, digest,
                 model, chunk_dim, vector, dbm.now()),
            )
        # 文档变短：删掉多余的旧块
        con.execute(
            "DELETE FROM embeddings WHERE source_type = ? AND source_id = ?"
            " AND model = ? AND chunk_no >= ?",
            (source_type, sid, model, len(chunks)),
        )
        # 主索引之外始终维护一份本地 hash 兜底索引（纯计算，不依赖外部服务）
        if model != HASH_MODEL:
            for i, chunk in enumerate(chunks):
                con.execute(
                    "INSERT OR REPLACE INTO embeddings(project_id, source_type, source_id,"
                    " source_version, chunk_no, text, content_hash, model, dim, vector,"
                    " state, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)",
                    (project or None, source_type, sid, version, i, chunk,
                     dbm.content_hash(chunk), HASH_MODEL, HASH_DIM,
                     json.dumps(tf(chunk)).encode(), dbm.now()),
                )
            con.execute(
                "DELETE FROM embeddings WHERE source_type = ? AND source_id = ?"
                " AND model = ? AND chunk_no >= ?",
                (source_type, sid, HASH_MODEL, len(chunks)),
            )
        # 换模型：其它稠密模型的旧向量标 stale（hash 兜底永不失效）
        con.execute(
            "UPDATE embeddings SET state = 'stale' WHERE source_type = ?"
            " AND source_id = ? AND model <> ? AND model <> ?",
            (source_type, sid, model, HASH_MODEL),
        )
        con.commit()

    return {"source_id": sid, "added": len(pending),
            "skipped": len(chunks) - len(pending), "model": model,
            "dim": dim, "degraded": degraded}


# ---------- 检索 ----------

_LAST_STATUS = {"degraded": False, "reason": ""}


def last_status():
    """最近一次 search 的降级状态，供 CLI/调度层显式提示。"""
    return dict(_LAST_STATUS)


def search(query, project=None, k=8, threshold=None, provider=None, source_types=None):
    """返回 [{score, source_id, chunk_no, text, model}]，按相似度降序。

    source_types 可选：只在这几类来源里检索（如 ("case",) 只查关单经验）。
    """
    global _LAST_STATUS
    _LAST_STATUS = {"degraded": False, "reason": ""}
    query = (query or "").strip()
    if not query:
        return []
    mode = pick_provider(provider)
    if mode == "ollama":
        hits, reason = _search_dense(
            query, project, k, threshold if threshold is not None else 0.3, source_types)
        if hits:
            return hits
        # 没有稠密索引或 Ollama 掉线：退回本地稀疏检索，并留下可查原因
        _LAST_STATUS = {"degraded": True,
                        "reason": reason or "稠密检索无命中（阈值过高或索引为空）"}
    return _search_sparse(query, project, k,
                          threshold if threshold is not None else 0.05, source_types)


def _rows(project, model, source_types=None):
    sql = ("SELECT source_id, chunk_no, text, vector, dim, project_id FROM embeddings"
           " WHERE model = ? AND state = 'active'")
    args = [model]
    if project:
        sql += " AND (project_id = ? OR project_id IS NULL)"
        args.append(project)
    if source_types:
        sql += " AND source_type IN (%s)" % ",".join("?" * len(source_types))
        args.extend(source_types)
    with closing(_connect()) as con:
        return con.execute(sql, args).fetchall()


matrix_cache = {}


def clear_matrix_cache():
    matrix_cache.clear()


def _matrix(project, source_types=None):
    """把稠密向量缓存成内存矩阵；条数或最大 id 变化时自动重建。

    口径与稀疏检索一致：项目视图 = 本项目 + project_id IS NULL 的公司级资产。
    只按 project_id = ? 过滤会漏掉公司模板（项目一旦有自己的稠密索引就查不到）。
    """
    types = tuple(source_types) if source_types else None
    key = (project or "*", types or "*")
    where = "model = ? AND state = 'active'"
    args = [EMBED_MODEL]
    if project:
        where += " AND (project_id = ? OR project_id IS NULL)"
        args.append(project)
    if types:
        where += " AND source_type IN (%s)" % ",".join("?" * len(types))
        args.extend(types)
    with closing(_connect()) as con:
        sig = con.execute(
            f"SELECT COUNT(*) AS n, COALESCE(MAX(id),0) AS m FROM embeddings WHERE {where}",
            args).fetchone()
        rows = con.execute(
            "SELECT source_id, chunk_no, text, vector, project_id FROM embeddings"
            f" WHERE {where} ORDER BY id", args).fetchall()
    signature = (sig["n"], sig["m"])
    entry = matrix_cache.get(key)
    if entry and entry["signature"] == signature:
        return entry
    vectors = [_unpack_dense(r["vector"]) for r in rows]
    entry = {
        "signature": signature,
        "rows": [(r["source_id"], r["chunk_no"], r["text"], r["project_id"])
                 for r in rows],
        "vectors": vectors,
    }
    matrix_cache[key] = entry
    return entry


def _dot(a, b):
    """余弦点积；不用 math.sumprod，保证 3.10/3.11 也能跑（它 3.12 才有）。"""
    if hasattr(math, "sumprod"):
        return math.sumprod(a, b)
    return sum(x * y for x, y in zip(a, b))


def _search_dense(query, project, k, threshold, source_types=None):
    entry = _matrix(project, source_types)
    if not entry["rows"]:
        return [], "无稠密索引"
    try:
        qvec = _normalize(_ollama_embed([query], timeout=20)[0])
    except (urllib.error.URLError, TimeoutError, OSError, KeyError) as e:
        return [], "Ollama 不可用：%s" % e
    out = []
    for (source_id, chunk_no, text, pid), vec in zip(entry["rows"], entry["vectors"]):
        if len(vec) != len(qvec):
            continue
        score = _dot(qvec, vec)
        if score >= threshold:
            out.append({"score": score, "source_id": source_id,
                        "chunk_no": chunk_no, "text": text,
                        "project_id": pid, "model": EMBED_MODEL})
    return sorted(out, key=lambda x: x["score"], reverse=True)[:k], ""


def _search_sparse(query, project, k, threshold, source_types=None):
    rows = _rows(project, HASH_MODEL, source_types)
    if not rows:
        return []
    docs = [(r, json.loads(r["vector"])) for r in rows]
    df = Counter(bucket for _, counts in docs for bucket in counts)
    n = len(docs)
    idf = {b: math.log((1 + n) / (1 + c)) + 1.0 for b, c in df.items()}
    qw = _sparse_weights(tf(query), idf)
    out = []
    for r, counts in docs:
        score = _cosine_sparse(qw, _sparse_weights(counts, idf))
        if score >= threshold:
            out.append({"score": score, "source_id": r["source_id"],
                        "chunk_no": r["chunk_no"], "text": r["text"],
                        "project_id": r["project_id"], "model": HASH_MODEL})
    return sorted(out, key=lambda x: x["score"], reverse=True)[:k]


# ---------- 维护 ----------

def delete_by_source(source_type, source_id):
    clear_matrix_cache()
    with closing(_connect()) as con, con:
        cur = con.execute(
            "DELETE FROM embeddings WHERE source_type = ? AND source_id = ?",
            (source_type, source_id),
        )
    return cur.rowcount


def status():
    with closing(_connect()) as con:
        rows = [dict(r) for r in con.execute(
            "SELECT model, state, COUNT(*) AS n FROM embeddings"
            " GROUP BY model, state ORDER BY model, state")]
        registry = [dict(r) for r in con.execute(
            "SELECT model, dim, provider, state FROM embedding_models ORDER BY model")]
    return {"rows": rows, "registry": registry, "ollama": ollama_available()}
