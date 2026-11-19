import { useCallback, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ApiError, apiClient } from '@/lib/api'
import './knowledge-workbench.css'

type Envelope<T> = { items: T[] }
type Collection = { collection_id: string; name: string; description: string; project_id?: string | null }
type DocumentItem = { document_id: string; title: string; version: number; source_uri: string; scope: string; media_type?: string; collection_id?: string | null }
type Chunk = { chunk_id: string; ordinal: number; text: string; token_count: number; char_count: number; page_number?: number | null; start_offset?: number | null; end_offset?: number | null; content_hash: string; metadata: Record<string, unknown>; embedding_status: string }
type IndexItem = { index_id: string; name: string; index_type: string; embedding_model: string; dimensions: number; status: string }
type Hit = { rank: number; document_id: string; document_version: number; chunk_id: string; title: string; source_locator: { page_number?: number | null; start_offset?: number | null; end_offset?: number | null }; text: string; score: number; citation_path: string }
type SearchResult = { result_count: number; elapsed_ms: number; strategy: string; top_k: number; items: Hit[] }
const basePath = 'platform/v1/knowledge'
const message = (error: unknown) => {
  if (error instanceof ApiError && error.status === 401) return '服务端要求身份认证，请检查 Knowledge principal 配置。'
  if (error instanceof ApiError && error.status === 403) return '当前身份没有访问或管理此知识资源的权限。'
  if (error instanceof ApiError && error.status === 404) return '资源不存在，或当前身份不可见。'
  if (error instanceof ApiError && error.status === 413) return '文件超过服务端上传大小限制。'
  if (error instanceof ApiError && error.status === 415) return '仅支持 PDF、Markdown 和 HTML 文件。'
  return error instanceof Error ? error.message : '请求失败，请稍后重试。'
}

export function KnowledgeWorkbench() {
  const { collectionId, documentId } = useParams()
  if (documentId) return <DocumentDetail documentId={documentId} />
  if (collectionId) return <CollectionDetail collectionId={collectionId} />
  return <KnowledgeCatalog />
}

function KnowledgeCatalog() {
  const [collections, setCollections] = useState<Collection[]>([])
  const [documents, setDocuments] = useState<DocumentItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [creating, setCreating] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [scope, setScope] = useState('tenant')
  const [collectionId, setCollectionId] = useState('')
  const [uploading, setUploading] = useState(false)
  const [uploadMessage, setUploadMessage] = useState('')
  const [query, setQuery] = useState('')
  const [strategy, setStrategy] = useState('hybrid')
  const [topK, setTopK] = useState(8)
  const [search, setSearch] = useState<SearchResult | null>(null)
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState('')

  const load = useCallback(async () => {
    setLoading(true); setError('')
    try {
      const [c, d] = await Promise.all([
        apiClient.get<Envelope<Collection>>(basePath + '/collections?limit=100'),
        apiClient.get<Envelope<DocumentItem>>(basePath + '/documents?limit=100'),
      ])
      setCollections(c.items ?? []); setDocuments(d.items ?? [])
    } catch (reason) { setError(message(reason)) } finally { setLoading(false) }
  }, [])
  useEffect(() => { void load() }, [load])

  const createCollection = async (event: FormEvent) => {
    event.preventDefault()
    setCreating(true); setError('')
    try {
      await apiClient.post(basePath + '/collections', { name: name.trim(), description })
      setName(''); setDescription(''); await load()
    } catch (reason) { setError(message(reason)) } finally { setCreating(false) }
  }
  const upload = async (event: FormEvent) => {
    event.preventDefault()
    if (!file) return
    setUploading(true); setUploadMessage(''); setError('')
    try {
      const params = new URLSearchParams({ filename: file.name, title: file.name.replace(/\.[^.]+$/, ''), scope })
      if (collectionId) params.set('collection_id', collectionId)
      await apiClient.post(basePath + '/documents/upload?' + params.toString(), file, {
        headers: { 'Content-Type': file.type || 'application/octet-stream' }, timeoutMs: 120000,
      })
      setUploadMessage('文档解析、切片与索引处理已完成。')
      setFile(null)
      const input = document.getElementById('knowledge-upload-file') as HTMLInputElement | null
      if (input) input.value = ''
      await load()
    } catch (reason) { setError(message(reason)) } finally { setUploading(false) }
  }
  const runSearch = async (event: FormEvent) => {
    event.preventDefault()
    if (!query.trim()) return
    setSearching(true); setSearchError('')
    try {
      const params = new URLSearchParams({ q: query.trim(), limit: String(Math.min(20, Math.max(1, topK))), strategy })
      setSearch(await apiClient.get<SearchResult>(basePath + '/search?' + params.toString()))
    } catch (reason) { setSearchError(message(reason)); setSearch(null) } finally { setSearching(false) }
  }

  return <section className="page-content knowledge-page">
    <header className="knowledge-heading"><div><p className="eyebrow">UI-G2-760 · KNOWLEDGE</p><h1>知识库管理</h1><p>管理知识集合、文档、切片与检索证据。数据来自 Knowledge API。</p></div><button className="button-secondary" onClick={() => void load()} disabled={loading}>刷新数据</button></header>
    {error && <div className="knowledge-alert" role="alert">{error}</div>}
    <div className="knowledge-stats"><article><span>知识集合</span><strong>{loading ? '—' : collections.length}</strong></article><article><span>文档</span><strong>{loading ? '—' : documents.length}</strong></article><article><span>已归入集合</span><strong>{loading ? '—' : documents.filter(d => d.collection_id).length}</strong></article></div>
    <div className="knowledge-columns">
      <section className="knowledge-panel"><div className="knowledge-panel-heading"><h2>知识集合</h2><span>{collections.length} 个</span></div>
        {loading ? <p className="knowledge-empty">正在加载…</p> : collections.length === 0 ? <p className="knowledge-empty">暂无集合。</p> : collections.map(item => <Link className="knowledge-list-item" to={'/operations/knowledge-bases/' + encodeURIComponent(item.collection_id)} key={item.collection_id}><span className="knowledge-item-icon">KB</span><span className="knowledge-item-copy"><strong>{item.name}</strong><small>{item.description || '暂无描述'}</small></span><span>→</span></Link>)}
        <form className="knowledge-form" onSubmit={event => void createCollection(event)}><h3>新建集合</h3><label>集合名称<input value={name} onChange={e => setName(e.target.value)} required maxLength={120} /></label><label>描述<textarea value={description} onChange={e => setDescription(e.target.value)} rows={2} maxLength={1000} /></label><button className="button-primary" disabled={creating || !name.trim()}>{creating ? '创建中…' : '创建集合'}</button></form>
      </section>
      <section className="knowledge-panel"><div className="knowledge-panel-heading"><h2>文档</h2><span>{documents.length} 个</span></div>
        {loading ? <p className="knowledge-empty">正在加载…</p> : documents.length === 0 ? <p className="knowledge-empty">暂无文档。</p> : documents.map(item => <Link className="knowledge-list-item" to={'/operations/documents/' + encodeURIComponent(item.document_id)} key={item.document_id}><span className="knowledge-item-icon document">DOC</span><span className="knowledge-item-copy"><strong>{item.title}</strong><small>v{item.version} · {item.scope} · {item.media_type || '未知类型'}</small></span><span>→</span></Link>)}
        <form className="knowledge-form" onSubmit={event => void upload(event)}><h3>上传并处理文档</h3><label>选择文件<input id="knowledge-upload-file" type="file" accept=".pdf,.md,.markdown,.html,.htm,application/pdf,text/markdown,text/html" onChange={e => setFile(e.target.files?.[0] ?? null)} /></label><label>访问范围<select value={scope} onChange={e => setScope(e.target.value)}><option value="tenant">实例共享（需管理权限）</option><option value="private">个人私有</option></select></label><label>归属集合<select value={collectionId} onChange={e => setCollectionId(e.target.value)}><option value="">不加入集合</option>{collections.map(c => <option key={c.collection_id} value={c.collection_id}>{c.name}</option>)}</select></label><p className="knowledge-help">当前 API 同步完成解析、切片和 Embedding；不展示虚构的后台进度或重试状态。</p>{uploadMessage && <p className="knowledge-success" role="status">{uploadMessage}</p>}<button className="button-primary" disabled={uploading || !file}>{uploading ? '正在处理…' : '上传并建立索引'}</button></form>
      </section>
    </div>
    <section className="knowledge-panel"><div className="knowledge-panel-heading"><h2>检索测试</h2><span>实时检索与引用穿透</span></div>
      <form className="knowledge-search-form" onSubmit={event => void runSearch(event)}><label className="knowledge-query">检索问题<input value={query} onChange={e => setQuery(e.target.value)} placeholder="输入问题，查看命中文档与引用证据" /></label><label>策略<select value={strategy} onChange={e => setStrategy(e.target.value)}><option value="hybrid">混合检索</option><option value="semantic">语义检索</option><option value="lexical">关键词检索</option></select></label><label>Top K<input type="number" min={1} max={20} value={topK} onChange={e => setTopK(Number(e.target.value))} /></label><button className="button-primary" disabled={searching || !query.trim()}>{searching ? '检索中…' : '执行检索'}</button></form>
      {searchError && <p className="knowledge-error" role="alert">{searchError}</p>}
      {search && <div className="knowledge-results"><p className="knowledge-result-summary">{search.result_count} 条 · {search.elapsed_ms} ms · {search.strategy} · Top {search.top_k}</p>{search.items.length === 0 ? <p className="knowledge-empty">没有找到匹配内容。</p> : search.items.map(hit => <article className="knowledge-result" key={hit.document_id + hit.chunk_id}><div className="knowledge-result-meta"><strong>#{hit.rank} {hit.title}</strong><span>score {Number(hit.score).toFixed(4)}</span></div><p>{hit.text}</p><small>版本 v{hit.document_version} · Chunk {hit.chunk_id} · {hit.source_locator.page_number ? '第 ' + hit.source_locator.page_number + ' 页' : 'offset ' + (hit.source_locator.start_offset ?? 0) + '–' + (hit.source_locator.end_offset ?? 0)}</small><Link className="text-link" to={'/operations/documents/' + encodeURIComponent(hit.document_id) + '?version=' + hit.document_version + '#chunk-' + encodeURIComponent(hit.chunk_id)}>查看引用与 Chunk →</Link></article>)}</div>}
    </section>
  </section>
}

function CollectionDetail({ collectionId }: { collectionId: string }) {
  const [collection, setCollection] = useState<Collection | null>(null)
  const [indexes, setIndexes] = useState<IndexItem[]>([])
  const [documents, setDocuments] = useState<DocumentItem[]>([])
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    Promise.all([
      apiClient.get<Collection>(basePath + '/collections/' + encodeURIComponent(collectionId)),
      apiClient.get<Envelope<IndexItem>>(basePath + '/collections/' + encodeURIComponent(collectionId) + '/indexes'),
      apiClient.get<Envelope<DocumentItem>>(basePath + '/documents?limit=500'),
    ]).then(([c, i, d]) => {
      if (!active) return
      setCollection(c); setIndexes(i.items ?? []); setDocuments((d.items ?? []).filter(x => x.collection_id === collectionId))
    }).catch(reason => { if (active) setError(message(reason)) })
    return () => { active = false }
  }, [collectionId])
  return <section className="page-content knowledge-page"><Link className="text-link" to="/operations/knowledge-bases">← 返回知识库</Link>{error ? <div className="knowledge-alert" role="alert">{error}</div> : !collection ? <div className="knowledge-panel">正在加载集合…</div> : <>
    <header className="knowledge-heading"><div><p className="eyebrow">KNOWLEDGE COLLECTION</p><h1>{collection.name}</h1><p>{collection.description || '暂无描述'}</p></div><span className="knowledge-status">集合详情</span></header>
    <div className="knowledge-stats"><article><span>文档</span><strong>{documents.length}</strong></article><article><span>索引</span><strong>{indexes.length}</strong></article><article><span>作用域</span><strong>{collection.project_id ? '项目' : '实例'}</strong></article></div>
    <section className="knowledge-panel"><div className="knowledge-panel-heading"><h2>索引版本</h2><span>服务端索引记录</span></div>{indexes.length === 0 ? <p className="knowledge-empty">此集合尚无索引记录。</p> : indexes.map(i => <div className="knowledge-list-item" key={i.index_id}><span className="knowledge-item-copy"><strong>{i.name}</strong><small>{i.index_type} · {i.embedding_model} · {i.dimensions} 维</small></span><span className="knowledge-status">{i.status}</span></div>)}</section>
    <section className="knowledge-panel"><div className="knowledge-panel-heading"><h2>集合文档</h2><span>{documents.length} 个</span></div>{documents.length === 0 ? <p className="knowledge-empty">没有找到关联文档。</p> : documents.map(d => <Link className="knowledge-list-item" to={'/operations/documents/' + encodeURIComponent(d.document_id)} key={d.document_id}><span className="knowledge-item-copy"><strong>{d.title}</strong><small>v{d.version} · {d.source_uri}</small></span><span>→</span></Link>)}</section>
  </>}</section>
}

function DocumentDetail({ documentId }: { documentId: string }) {
  const [documents, setDocuments] = useState<DocumentItem[]>([])
  const [chunks, setChunks] = useState<Chunk[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const selectedChunk = window.location.hash.startsWith('#chunk-') ? window.location.hash.slice(7) : ''
  useEffect(() => {
    let active = true
    setLoading(true); setError('')
    Promise.all([
      apiClient.get<Envelope<DocumentItem>>(basePath + '/documents?limit=500'),
      apiClient.get<Envelope<Chunk>>(basePath + '/documents/' + encodeURIComponent(documentId) + '/chunks'),
    ]).then(([d, c]) => { if (active) { setDocuments(d.items ?? []); setChunks(c.items ?? []) } })
      .catch(reason => { if (active) setError(message(reason)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [documentId])
  useEffect(() => { if (!loading && selectedChunk) window.setTimeout(() => document.getElementById('chunk-' + selectedChunk)?.scrollIntoView({ block: 'center' }), 0) }, [loading, selectedChunk, chunks])
  const item = documents.find(d => d.document_id === documentId)
  return <section className="page-content knowledge-page"><Link className="text-link" to="/operations/knowledge-bases">← 返回知识库</Link>{loading ? <div className="knowledge-panel">正在加载文档与 Chunk…</div> : error ? <div className="knowledge-alert" role="alert">{error}</div> : <>
    <header className="knowledge-heading"><div><p className="eyebrow">DOCUMENT · {documentId}</p><h1>{item?.title ?? '文档详情'}</h1><p>{item ? item.source_uri + ' · ' + item.scope + ' · v' + item.version : '文档元数据暂不可用'}</p></div><span className="knowledge-status">{chunks.length} 个 Chunk</span></header>
    <section className="knowledge-panel"><div className="knowledge-panel-heading"><h2>Chunk 预览</h2><span>来源定位、Hash 与 Metadata</span></div>{chunks.length === 0 ? <p className="knowledge-empty">此文档当前没有可预览的 Chunk。</p> : chunks.map(c => <article className="knowledge-chunk" id={'chunk-' + c.chunk_id} key={c.chunk_id}><div className="knowledge-result-meta"><strong>#{c.ordinal} · {c.chunk_id}</strong><span>{c.embedding_status}</span></div><p>{c.text}</p><div className="knowledge-chunk-meta"><span>{c.token_count} tokens</span><span>{c.char_count} chars</span><span>{c.page_number ? '第 ' + c.page_number + ' 页' : '无页码'}</span><span>offset {c.start_offset ?? 0}–{c.end_offset ?? 0}</span></div><details><summary>查看 metadata / content hash</summary><pre>{JSON.stringify({ content_hash: c.content_hash, metadata: c.metadata }, null, 2)}</pre></details></article>)}</section>
  </>}</section>
}
