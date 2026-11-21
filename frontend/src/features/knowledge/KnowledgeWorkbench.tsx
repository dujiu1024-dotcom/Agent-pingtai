import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { ApiError, apiClient } from '@/lib/api'
import { localizedJson, valueLabel } from '@/lib/i18n/labels'
import './knowledge-workbench.css'

type Envelope<T> = { items: T[] }
type Collection = { collection_id: string; name: string; description: string; project_id?: string | null }
type DocumentItem = { document_id: string; title: string; version: number; source_uri: string; scope: string; media_type?: string; collection_id?: string | null }
type Chunk = { chunk_id: string; ordinal: number; text: string; token_count: number; char_count: number; page_number?: number | null; start_offset?: number | null; end_offset?: number | null; content_hash: string; metadata: Record<string, unknown>; embedding_status: string }
type IndexItem = { index_id: string; name: string; index_type: string; embedding_model: string; dimensions: number; status: string }
type Hit = { rank: number; document_id: string; document_version: number; chunk_id: string; title: string; source_locator: { page_number?: number | null; start_offset?: number | null; end_offset?: number | null }; text: string; score: number; citation_path: string }
type SearchResult = { result_count: number; elapsed_ms: number; strategy: string; top_k: number; items: Hit[] }
type UploadOptions = {
  supported_media_types: string[]
  default_max_chars: number
  default_overlap: number
  min_max_chars: number
  max_upload_bytes: number
  embedding_model: string | null
  embedding_configured: boolean
}
type Citation = {
  citation_id: string
  document_id: string
  document_version: number
  chunk_id: string
  title: string | null
  source_uri?: string
  page_number?: number | null
  start_offset?: number | null
  end_offset?: number | null
  text: string
  score?: number
}
type AnswerResult = { answer: string; citations: Citation[]; abstained: boolean; reason: string | null }
type AclGrant = { principal_type: string; principal_id: string; permission: string }
type ChunkAclGrant = AclGrant & { chunk_id: string }
const basePath = 'platform/v1/knowledge'
const EXTENSION_MEDIA_TYPES: Record<string, string> = {
  '.pdf': 'application/pdf',
  '.md': 'text/markdown',
  '.markdown': 'text/markdown',
  '.html': 'text/html',
  '.htm': 'text/html',
}
const UPLOAD_MEDIA_TYPES = new Set(['application/pdf', 'text/markdown', 'text/html'])
const MEDIA_TYPE_ALIASES: Record<string, string> = {
  'text/x-markdown': 'text/markdown',
  'application/xhtml+xml': 'text/html',
}
const MEDIA_TYPE_LABELS: Record<string, string> = {
  'application/pdf': 'PDF',
  'text/markdown': 'Markdown',
  'text/x-markdown': 'Markdown',
  'text/html': 'HTML',
  'application/xhtml+xml': 'HTML',
  'text/plain': '纯文本',
}
const ANSWER_REASON_LABELS: Record<string, string> = {
  no_authorized_evidence: '没有可用的授权证据',
  empty_answer: '模型返回空答案',
  citations_required: '答案缺少引用',
  unknown_citation: '答案引用了未知来源',
}
const mediaTypeLabel = (value: string) => MEDIA_TYPE_LABELS[value] ?? value
const formatBytes = (bytes: number) => {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`
  return `${bytes} B`
}
const message = (error: unknown) => {
  if (error instanceof ApiError && error.status === 401) return '服务端要求身份认证，请检查 Knowledge principal 配置。'
  if (error instanceof ApiError && error.status === 403) return '当前身份没有访问或管理此知识资源的权限。'
  if (error instanceof ApiError && error.status === 404) return '资源不存在，或当前身份不可见。'
  if (error instanceof ApiError && error.status === 409) return '当前状态与请求冲突：作用域/集合成员不可原地变更，或记录已存在。'
  if (error instanceof ApiError && error.status === 413) return '文件超过服务端上传大小限制。'
  if (error instanceof ApiError && error.status === 415) return '仅支持 PDF、Markdown 和 HTML 文件。'
  if (error instanceof ApiError && error.status === 422) return '请求参数或引用未通过服务端校验，请检查主体、权限与切片标识。'
  if (error instanceof ApiError && error.status === 503) return '服务未启用或依赖不可用（Embedding / 生成模型），请稍后重试。'
  return error instanceof Error ? error.message : '请求失败，请稍后重试。'
}

export function KnowledgeWorkbench() {
  const { collectionId, documentId } = useParams()
  if (documentId) return <DocumentDetail documentId={documentId} />
  if (collectionId) return <CollectionDetail collectionId={collectionId} />
  return <KnowledgeCatalog />
}

type KnowledgeTab = 'catalog' | 'retrieve' | 'answer'

function KnowledgeCatalog() {
  const queryClient = useQueryClient()
  const [tab, setTab] = useState<KnowledgeTab>('catalog')
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [scope, setScope] = useState('tenant')
  const [collectionId, setCollectionId] = useState('')
  const [uploadMessage, setUploadMessage] = useState('')
  const [uploadError, setUploadError] = useState('')
  const [query, setQuery] = useState('')
  const [strategy, setStrategy] = useState('hybrid')
  const [topK, setTopK] = useState(8)
  const [search, setSearch] = useState<SearchResult | null>(null)
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState('')
  const [question, setQuestion] = useState('')
  const [answerLimit, setAnswerLimit] = useState(5)

  const collectionsQuery = useQuery({
    queryKey: ['knowledge', 'collections'],
    queryFn: () => apiClient.get<Envelope<Collection>>(basePath + '/collections?limit=100'),
  })
  const documentsQuery = useQuery({
    queryKey: ['knowledge', 'documents', 'catalog'],
    queryFn: () => apiClient.get<Envelope<DocumentItem>>(basePath + '/documents?limit=100'),
  })
  const uploadOptionsQuery = useQuery({
    queryKey: ['knowledge', 'upload-options'],
    queryFn: () => apiClient.get<UploadOptions>(basePath + '/upload-options'),
    staleTime: 5 * 60_000,
  })

  const collections = collectionsQuery.data?.items ?? []
  const documents = documentsQuery.data?.items ?? []
  const loading = collectionsQuery.isPending || documentsQuery.isPending
  const loadError = collectionsQuery.error ?? documentsQuery.error ?? null
  const uploadOptions = uploadOptionsQuery.data

  const validateFile = (candidate: File): string => {
    const declaredRaw = candidate.type && candidate.type !== 'application/octet-stream' ? candidate.type : ''
    const declared = declaredRaw ? MEDIA_TYPE_ALIASES[declaredRaw] ?? declaredRaw : ''
    const index = candidate.name.lastIndexOf('.')
    const suffix = index >= 0 ? candidate.name.slice(index).toLowerCase() : ''
    if (suffix && !EXTENSION_MEDIA_TYPES[suffix]) {
      return `文件名扩展名 ${suffix} 不受支持，仅支持 .pdf / .md / .markdown / .html / .htm。`
    }
    const expected = suffix ? EXTENSION_MEDIA_TYPES[suffix] : ''
    const mediaType = declared || expected
    if (!mediaType) return '无法识别该文件类型，请选择 PDF、Markdown 或 HTML 文件。'
    if (!UPLOAD_MEDIA_TYPES.has(mediaType)) {
      return `上传接口不支持 ${mediaType}，仅支持 PDF、Markdown 和 HTML。`
    }
    if (expected && declared && declared !== expected) {
      return '文件扩展名与 Content-Type 不一致，请确认文件类型后重试。'
    }
    if (!uploadOptions) return ''
    if (candidate.size > uploadOptions.max_upload_bytes) {
      return `文件大小 ${formatBytes(candidate.size)} 超过服务端上限 ${formatBytes(uploadOptions.max_upload_bytes)}。`
    }
    const supported = uploadOptions.supported_media_types ?? []
    const supportedLabel = supported.length ? supported.map(mediaTypeLabel).join('、') : '未声明'
    if (supported.length > 0 && !supported.includes(mediaType)) {
      return `服务端未配置 ${mediaTypeLabel(mediaType)} 解析器，当前支持：${supportedLabel}。`
    }
    return ''
  }

  const createCollectionMutation = useMutation({
    mutationFn: (input: { name: string; description: string }) =>
      apiClient.post(basePath + '/collections', input),
    onSuccess: () => {
      setName('')
      setDescription('')
      void queryClient.invalidateQueries({ queryKey: ['knowledge', 'collections'] })
    },
  })
  const uploadMutation = useMutation({
    mutationFn: async (input: { file: File; scope: string; collectionId: string }) => {
      const params = new URLSearchParams({
        filename: input.file.name,
        title: input.file.name.replace(/\.[^.]+$/, ''),
        scope: input.scope,
      })
      if (input.collectionId) params.set('collection_id', input.collectionId)
      return apiClient.post(basePath + '/documents/upload?' + params.toString(), input.file, {
        headers: { 'Content-Type': input.file.type || 'application/octet-stream' },
        timeoutMs: 120000,
      })
    },
    onSuccess: () => {
      setUploadMessage('文档解析、切片与索引处理已完成。')
      setFile(null)
      const input = document.getElementById('knowledge-upload-file') as HTMLInputElement | null
      if (input) input.value = ''
      void queryClient.invalidateQueries({ queryKey: ['knowledge', 'documents'] })
      void queryClient.invalidateQueries({ queryKey: ['knowledge', 'collections'] })
    },
  })
  const answerMutation = useMutation({
    mutationFn: (input: { query: string; limit: number }) =>
      apiClient.post<AnswerResult>(basePath + '/answer', input),
  })

  const createCollection = (event: FormEvent) => {
    event.preventDefault()
    createCollectionMutation.mutate({ name: name.trim(), description })
  }
  const upload = (event: FormEvent) => {
    event.preventDefault()
    if (!file) return
    const validation = validateFile(file)
    if (validation) {
      setUploadError(validation)
      return
    }
    setUploadMessage('')
    setUploadError('')
    uploadMutation.mutate({ file, scope, collectionId })
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
  const askQuestion = (event: FormEvent) => {
    event.preventDefault()
    if (!question.trim()) return
    answerMutation.mutate({ query: question.trim(), limit: Math.min(20, Math.max(1, answerLimit)) })
  }

  return <section className="page-content knowledge-page">
    <header className="knowledge-heading"><div><p className="eyebrow">知识中枢</p><h1>知识库管理</h1><p>管理知识集合、文档、切片与检索证据。数据来自 Knowledge API。</p></div><button className="button-secondary" onClick={() => { void collectionsQuery.refetch(); void documentsQuery.refetch() }} disabled={loading}>刷新数据</button></header>
    {loadError && <div className="knowledge-alert" role="alert">{message(loadError)}</div>}
    <div className="knowledge-stats"><article><span>知识集合</span><strong>{loading ? '—' : collections.length}</strong></article><article><span>文档</span><strong>{loading ? '—' : documents.length}</strong></article><article><span>已归入集合</span><strong>{loading ? '—' : documents.filter(d => d.collection_id).length}</strong></article></div>

    <nav className="panel-tabs" role="tablist" aria-label="知识库分区">
      <button type="button" role="tab" aria-selected={tab === 'catalog'} className={tab === 'catalog' ? 'panel-tab active' : 'panel-tab'} onClick={() => setTab('catalog')}>
        集合与文档<span className="panel-tab-count">{loading ? '—' : collections.length + documents.length}</span>
      </button>
      <button type="button" role="tab" aria-selected={tab === 'retrieve'} className={tab === 'retrieve' ? 'panel-tab active' : 'panel-tab'} onClick={() => setTab('retrieve')}>
        检索测试
      </button>
      <button type="button" role="tab" aria-selected={tab === 'answer'} className={tab === 'answer' ? 'panel-tab active' : 'panel-tab'} onClick={() => setTab('answer')}>
        知识问答
      </button>
    </nav>

    {tab === 'catalog' && (
      <div className="knowledge-columns">
      <section className="knowledge-panel"><div className="knowledge-panel-heading"><h2>知识集合</h2><span>{collections.length} 个</span></div>
        {loading ? <p className="knowledge-empty">正在加载…</p> : collections.length === 0 ? <p className="knowledge-empty">暂无集合。</p> : collections.map(item => <Link className="knowledge-list-item" to={'/operations/knowledge-bases/' + encodeURIComponent(item.collection_id)} key={item.collection_id}><span className="knowledge-item-icon">库</span><span className="knowledge-item-copy"><strong>{item.name}</strong><small>{item.description || '暂无描述'}</small></span><span>→</span></Link>)}
        <form className="knowledge-form" onSubmit={createCollection}><h3>新建集合</h3><label>集合名称<input value={name} onChange={e => setName(e.target.value)} required maxLength={120} /></label><label>描述<textarea value={description} onChange={e => setDescription(e.target.value)} rows={2} maxLength={1000} /></label>{createCollectionMutation.isError && <p className="knowledge-error" role="alert">{message(createCollectionMutation.error)}</p>}{createCollectionMutation.isSuccess && <p className="knowledge-success" role="status">集合已创建。</p>}<button className="button-primary" disabled={createCollectionMutation.isPending || !name.trim()}>{createCollectionMutation.isPending ? '创建中…' : '创建集合'}</button></form>
      </section>
      <section className="knowledge-panel"><div className="knowledge-panel-heading"><h2>文档</h2><span>{documents.length} 个</span></div>
        {loading ? <p className="knowledge-empty">正在加载…</p> : documents.length === 0 ? <p className="knowledge-empty">暂无文档。</p> : documents.map(item => <Link className="knowledge-list-item" to={'/operations/documents/' + encodeURIComponent(item.document_id)} key={item.document_id}><span className="knowledge-item-icon document">文档</span><span className="knowledge-item-copy"><strong>{item.title}</strong><small>v{item.version} · {valueLabel(item.scope)} · {item.media_type || '未知类型'}</small></span><span>→</span></Link>)}
        <form className="knowledge-form" onSubmit={upload}><h3>上传并处理文档</h3><label>选择文件<input id="knowledge-upload-file" type="file" accept=".pdf,.md,.markdown,.html,.htm,application/pdf,text/markdown,text/html" onChange={e => { const next = e.target.files?.[0] ?? null; setFile(next); setUploadMessage(''); setUploadError(next ? validateFile(next) : '') }} /></label><label>访问范围<select value={scope} onChange={e => setScope(e.target.value)}><option value="tenant">实例共享（需管理权限）</option><option value="private">个人私有</option></select></label><label>归属集合<select value={collectionId} onChange={e => setCollectionId(e.target.value)}><option value="">不加入集合</option>{collections.map(c => <option key={c.collection_id} value={c.collection_id}>{c.name}</option>)}</select></label>
          {uploadOptionsQuery.isPending ? <p className="knowledge-help">正在读取服务端上传限制…</p> : uploadOptionsQuery.isError ? <p className="knowledge-error" role="alert">{message(uploadOptionsQuery.error)}</p> : uploadOptions && <div className="knowledge-upload-options">
            <p className="knowledge-help">服务端支持：{uploadOptions.supported_media_types.length ? uploadOptions.supported_media_types.map(mediaTypeLabel).join('、') : '未声明'}；单文件上限 {formatBytes(uploadOptions.max_upload_bytes)}；默认切片 {uploadOptions.default_max_chars} 字符 / 重叠 {uploadOptions.default_overlap}；Embedding：{uploadOptions.embedding_model ?? '未配置'}。</p>
            {!uploadOptions.embedding_configured && <p className="knowledge-error" role="alert">服务端未配置 Embedding，上传会返回 503。</p>}
          </div>}
          {uploadError && <p className="knowledge-error" role="alert">{uploadError}</p>}
          {uploadMutation.isError && <p className="knowledge-error" role="alert">{message(uploadMutation.error)}</p>}
          <p className="knowledge-help">当前 API 同步完成解析、切片和 Embedding；不展示虚构的后台进度或重试状态。</p>{uploadMessage && <p className="knowledge-success" role="status">{uploadMessage}</p>}<button className="button-primary" disabled={uploadMutation.isPending || !file || Boolean(uploadError)}>{uploadMutation.isPending ? '正在处理…' : '上传并建立索引'}</button></form>
      </section>
    </div>
    )}

    {tab === 'retrieve' && (
    <section className="knowledge-panel"><div className="knowledge-panel-heading"><h2>检索测试</h2><span>实时检索与引用穿透</span></div>
      <form className="knowledge-search-form" onSubmit={event => void runSearch(event)}><label className="knowledge-query">检索问题<input value={query} onChange={e => setQuery(e.target.value)} placeholder="输入问题，查看命中文档与引用证据" /></label><label>策略<select value={strategy} onChange={e => setStrategy(e.target.value)}><option value="hybrid">混合检索</option><option value="semantic">语义检索</option><option value="lexical">关键词检索</option></select></label><label>返回条数<input type="number" min={1} max={20} value={topK} onChange={e => setTopK(Number(e.target.value))} /></label><button className="button-primary" disabled={searching || !query.trim()}>{searching ? '检索中…' : '执行检索'}</button></form>
      {searchError && <p className="knowledge-error" role="alert">{searchError}</p>}
      {search && <div className="knowledge-results"><p className="knowledge-result-summary">{search.result_count} 条 · {search.elapsed_ms} 毫秒 · {valueLabel(search.strategy)} · 前 {search.top_k} 条</p>{search.items.length === 0 ? <p className="knowledge-empty">没有找到匹配内容。</p> : search.items.map(hit => <article className="knowledge-result" key={hit.document_id + hit.chunk_id}><div className="knowledge-result-meta"><strong>#{hit.rank} {hit.title}</strong><span>评分 {Number(hit.score).toFixed(4)}</span></div><p>{hit.text}</p><small>版本 v{hit.document_version} · 切片 {hit.chunk_id} · {hit.source_locator.page_number ? '第 ' + hit.source_locator.page_number + ' 页' : '偏移 ' + (hit.source_locator.start_offset ?? 0) + '–' + (hit.source_locator.end_offset ?? 0)}</small><Link className="text-link" to={'/operations/documents/' + encodeURIComponent(hit.document_id) + '?version=' + hit.document_version + '#chunk-' + encodeURIComponent(hit.chunk_id)}>查看引用与切片 →</Link></article>)}</div>}
    </section>
    )}

    {tab === 'answer' && (
    <section className="knowledge-panel" aria-labelledby="knowledge-answer-title"><div className="knowledge-panel-heading"><h2 id="knowledge-answer-title">知识问答</h2><span>调用 RAG 答案接口并展示引用</span></div>
      <form className="knowledge-search-form knowledge-answer-form" onSubmit={askQuestion}><label className="knowledge-query">问题<input value={question} onChange={e => setQuestion(e.target.value)} placeholder="例如：文档中如何配置权限？" maxLength={4096} /></label><label>返回条数<input type="number" min={1} max={20} value={answerLimit} onChange={e => setAnswerLimit(Number(e.target.value))} /></label><button className="button-primary" disabled={answerMutation.isPending || !question.trim()}>{answerMutation.isPending ? '生成中…' : '提问'}</button></form>
      {answerMutation.isError && <p className="knowledge-error" role="alert">{message(answerMutation.error)}</p>}
      {answerMutation.data && <div className="knowledge-answer">
        <div className="knowledge-result-meta"><strong>答案</strong><span>{answerMutation.data.abstained ? '已拒答' : '已生成'}</span></div>
        {answerMutation.data.abstained ? <p className="knowledge-answer-text">服务端未给出答案：{answerMutation.data.reason ? ANSWER_REASON_LABELS[answerMutation.data.reason] ?? answerMutation.data.reason : '未提供原因'}。</p> : <p className="knowledge-answer-text">{answerMutation.data.answer}</p>}
        <h3>引用（{answerMutation.data.citations.length}）</h3>
        {answerMutation.data.citations.length === 0 ? <p className="knowledge-empty">本次回答没有返回引用。</p> : answerMutation.data.citations.map(citation => <article className="knowledge-citation" key={citation.citation_id}>
          <div className="knowledge-result-meta"><strong>{citation.title || citation.document_id}</strong><span>{typeof citation.score === 'number' ? '评分 ' + citation.score.toFixed(4) : '已引用'}</span></div>
          <p>{citation.text}</p>
          <small>版本 v{citation.document_version} · 切片 {citation.chunk_id} · {citation.page_number ? '第 ' + citation.page_number + ' 页' : '偏移 ' + (citation.start_offset ?? 0) + '–' + (citation.end_offset ?? 0)}</small>
          <Link className="text-link" to={'/operations/documents/' + encodeURIComponent(citation.document_id) + '?version=' + citation.document_version + '#chunk-' + encodeURIComponent(citation.chunk_id)}>查看引用切片 →</Link>
        </article>)}
      </div>}
    </section>
    )}
  </section>
}

function CollectionDetail({ collectionId }: { collectionId: string }) {
  const collectionQuery = useQuery({
    queryKey: ['knowledge', 'collection', collectionId],
    queryFn: () => apiClient.get<Collection>(basePath + '/collections/' + encodeURIComponent(collectionId)),
  })
  const indexesQuery = useQuery({
    queryKey: ['knowledge', 'indexes', collectionId],
    queryFn: () => apiClient.get<Envelope<IndexItem>>(basePath + '/collections/' + encodeURIComponent(collectionId) + '/indexes'),
  })
  const documentsQuery = useQuery({
    queryKey: ['knowledge', 'documents', 'all'],
    queryFn: () => apiClient.get<Envelope<DocumentItem>>(basePath + '/documents?limit=500'),
  })
  const collection = collectionQuery.data
  const indexes = indexesQuery.data?.items ?? []
  const documents = (documentsQuery.data?.items ?? []).filter(x => x.collection_id === collectionId)
  const error = collectionQuery.error ?? indexesQuery.error ?? documentsQuery.error ?? null
  return <section className="page-content knowledge-page"><Link className="text-link" to="/operations/knowledge-bases">← 返回知识库</Link>{error ? <div className="knowledge-alert" role="alert">{message(error)}</div> : !collection ? <div className="knowledge-panel">正在加载集合…</div> : <>
    <header className="knowledge-heading"><div><p className="eyebrow">知识集合</p><h1>{collection.name}</h1><p>{collection.description || '暂无描述'}</p></div><span className="knowledge-status">集合详情</span></header>
    <div className="knowledge-stats"><article><span>文档</span><strong>{documents.length}</strong></article><article><span>索引</span><strong>{indexes.length}</strong></article><article><span>作用域</span><strong>{collection.project_id ? '项目' : '实例'}</strong></article></div>
    <section className="knowledge-panel"><div className="knowledge-panel-heading"><h2>索引版本</h2><span>服务端索引记录</span></div>{indexes.length === 0 ? <p className="knowledge-empty">此集合尚无索引记录。</p> : indexes.map(i => <div className="knowledge-list-item" key={i.index_id}><span className="knowledge-item-copy"><strong>{i.name}</strong><small>{valueLabel(i.index_type)} · {i.embedding_model} · {i.dimensions} 维</small></span><span className="knowledge-status">{valueLabel(i.status)}</span></div>)}</section>
    <section className="knowledge-panel"><div className="knowledge-panel-heading"><h2>集合文档</h2><span>{documents.length} 个</span></div>{documents.length === 0 ? <p className="knowledge-empty">没有找到关联文档。</p> : documents.map(d => <Link className="knowledge-list-item" to={'/operations/documents/' + encodeURIComponent(d.document_id)} key={d.document_id}><span className="knowledge-item-copy"><strong>{d.title}</strong><small>v{d.version} · {d.source_uri}</small></span><span>→</span></Link>)}</section>
  </>}</section>
}

function DocumentDetail({ documentId }: { documentId: string }) {
  const queryClient = useQueryClient()
  const [docPrincipalType, setDocPrincipalType] = useState('user')
  const [docPrincipalId, setDocPrincipalId] = useState('')
  const [docPermission, setDocPermission] = useState('read')
  const [docGrants, setDocGrants] = useState<AclGrant[]>([])
  const [chunkAclChunkId, setChunkAclChunkId] = useState('')
  const [chunkPrincipalType, setChunkPrincipalType] = useState('user')
  const [chunkPrincipalId, setChunkPrincipalId] = useState('')
  const [chunkPermission, setChunkPermission] = useState('read')
  const [chunkGrants, setChunkGrants] = useState<ChunkAclGrant[]>([])
  const documentsQuery = useQuery({
    queryKey: ['knowledge', 'documents', 'all'],
    queryFn: () => apiClient.get<Envelope<DocumentItem>>(basePath + '/documents?limit=500'),
  })
  const chunksQuery = useQuery({
    queryKey: ['knowledge', 'chunks', documentId],
    queryFn: () => apiClient.get<Envelope<Chunk>>(basePath + '/documents/' + encodeURIComponent(documentId) + '/chunks'),
  })
  const loading = documentsQuery.isPending || chunksQuery.isPending
  const error = documentsQuery.error ?? chunksQuery.error ?? null
  const documents = documentsQuery.data?.items ?? []
  const chunks = chunksQuery.data?.items ?? []
  const selectedChunk = window.location.hash.startsWith('#chunk-') ? window.location.hash.slice(7) : ''
  const item = documents.find(d => d.document_id === documentId)

  useEffect(() => { if (!loading && selectedChunk) window.setTimeout(() => document.getElementById('chunk-' + selectedChunk)?.scrollIntoView({ block: 'center' }), 0) }, [loading, selectedChunk, chunks])

  const invalidateDocumentAccess = () => {
    void queryClient.invalidateQueries({ queryKey: ['knowledge', 'documents'] })
    void queryClient.invalidateQueries({ queryKey: ['knowledge', 'chunks', documentId] })
  }
  const grantDocumentAcl = useMutation({
    mutationFn: (input: AclGrant) =>
      apiClient.post(basePath + '/documents/' + encodeURIComponent(documentId) + '/acl', input),
    onSuccess: (_result, input) => {
      setDocGrants(previous => [input, ...previous.filter(grant => !(grant.principal_type === input.principal_type && grant.principal_id === input.principal_id && grant.permission === input.permission))])
      invalidateDocumentAccess()
    },
  })
  const revokeDocumentAcl = useMutation({
    mutationFn: (input: AclGrant) =>
      apiClient.delete(basePath + '/documents/' + encodeURIComponent(documentId) + '/acl/' + encodeURIComponent(input.principal_type) + '/' + encodeURIComponent(input.principal_id) + '?permission=' + encodeURIComponent(input.permission)),
    onSuccess: (_result, input) => {
      setDocGrants(previous => previous.filter(grant => !(grant.principal_type === input.principal_type && grant.principal_id === input.principal_id && grant.permission === input.permission)))
      invalidateDocumentAccess()
    },
  })
  const grantChunkAcl = useMutation({
    mutationFn: (input: ChunkAclGrant) =>
      apiClient.post(basePath + '/documents/' + encodeURIComponent(documentId) + '/chunks/' + encodeURIComponent(input.chunk_id) + '/acl', {
        principal_type: input.principal_type,
        principal_id: input.principal_id,
        permission: input.permission,
      }),
    onSuccess: (_result, input) => {
      setChunkGrants(previous => [input, ...previous.filter(grant => !(grant.chunk_id === input.chunk_id && grant.principal_type === input.principal_type && grant.principal_id === input.principal_id && grant.permission === input.permission))])
      invalidateDocumentAccess()
    },
  })
  const revokeChunkAcl = useMutation({
    mutationFn: (input: ChunkAclGrant) =>
      apiClient.delete(basePath + '/documents/' + encodeURIComponent(documentId) + '/chunks/' + encodeURIComponent(input.chunk_id) + '/acl/' + encodeURIComponent(input.principal_type) + '/' + encodeURIComponent(input.principal_id) + '?permission=' + encodeURIComponent(input.permission)),
    onSuccess: (_result, input) => {
      setChunkGrants(previous => previous.filter(grant => !(grant.chunk_id === input.chunk_id && grant.principal_type === input.principal_type && grant.principal_id === input.principal_id && grant.permission === input.permission)))
      invalidateDocumentAccess()
    },
  })

  const submitDocumentAcl = (event: FormEvent) => {
    event.preventDefault()
    if (!docPrincipalId.trim()) return
    grantDocumentAcl.mutate({ principal_type: docPrincipalType, principal_id: docPrincipalId.trim(), permission: docPermission })
  }
  const submitChunkAcl = (event: FormEvent) => {
    event.preventDefault()
    if (!chunkAclChunkId || !chunkPrincipalId.trim()) return
    grantChunkAcl.mutate({ chunk_id: chunkAclChunkId, principal_type: chunkPrincipalType, principal_id: chunkPrincipalId.trim(), permission: chunkPermission })
  }

  return <section className="page-content knowledge-page"><Link className="text-link" to="/operations/knowledge-bases">← 返回知识库</Link>{loading ? <div className="knowledge-panel">正在加载文档与切片…</div> : error ? <div className="knowledge-alert" role="alert">{message(error)}</div> : <>
    <header className="knowledge-heading"><div><p className="eyebrow">文档 · {documentId}</p><h1>{item?.title ?? '文档详情'}</h1><p>{item ? item.source_uri + ' · ' + valueLabel(item.scope) + ' · v' + item.version : '文档元数据暂不可用'}</p></div><span className="knowledge-status">{chunks.length} 个切片</span></header>
    <section className="knowledge-panel"><div className="knowledge-panel-heading"><h2>切片预览</h2><span>来源定位、哈希与元数据</span></div>{chunks.length === 0 ? <p className="knowledge-empty">此文档当前没有可预览的切片。</p> : chunks.map(c => <article className="knowledge-chunk" id={'chunk-' + c.chunk_id} key={c.chunk_id}><div className="knowledge-result-meta"><strong>#{c.ordinal} · {c.chunk_id}</strong><span>{valueLabel(c.embedding_status)}</span></div><p>{c.text}</p><div className="knowledge-chunk-meta"><span>{c.token_count} 词元</span><span>{c.char_count} 字符</span><span>{c.page_number ? '第 ' + c.page_number + ' 页' : '无页码'}</span><span>偏移 {c.start_offset ?? 0}–{c.end_offset ?? 0}</span></div><details><summary>查看元数据 / 内容哈希</summary><pre>{localizedJson({ content_hash: c.content_hash, metadata: c.metadata })}</pre></details></article>)}</section>
    <section className="knowledge-panel" aria-labelledby="knowledge-doc-acl-title"><div className="knowledge-panel-heading"><h2 id="knowledge-doc-acl-title">文档访问控制（ACL）</h2><span>主体 · 权限</span></div>
      <p className="knowledge-help">当前文档作用域：{item ? valueLabel(item.scope) : '未知'} · 版本 v{item?.version ?? '—'}。服务端仅提供授权与撤销接口，未提供 ACL 列表查询接口；下方展示本页已提交的授权记录（来自真实接口响应），可逐条撤销。</p>
      <form className="knowledge-acl-form knowledge-acl-form-document" onSubmit={submitDocumentAcl}>
        <label>主体类型<select value={docPrincipalType} onChange={e => setDocPrincipalType(e.target.value)}><option value="user">用户</option><option value="role">角色</option><option value="project">项目</option></select></label>
        <label>主体 ID<input value={docPrincipalId} onChange={e => setDocPrincipalId(e.target.value)} placeholder="用户 / 角色 / 项目 ID" required /></label>
        <label>权限<select value={docPermission} onChange={e => setDocPermission(e.target.value)}><option value="read">只读</option><option value="manage">管理</option></select></label>
        <div className="knowledge-acl-actions">
          <button className="button-primary" type="submit" disabled={grantDocumentAcl.isPending || !docPrincipalId.trim()}>{grantDocumentAcl.isPending ? '提交中…' : '授予权限'}</button>
          <button className="button-secondary" type="button" onClick={() => { if (docPrincipalId.trim()) revokeDocumentAcl.mutate({ principal_type: docPrincipalType, principal_id: docPrincipalId.trim(), permission: docPermission }) }} disabled={revokeDocumentAcl.isPending || !docPrincipalId.trim()}>{revokeDocumentAcl.isPending ? '撤销中…' : '撤销此授权'}</button>
        </div>
      </form>
      {grantDocumentAcl.isError && <p className="knowledge-error" role="alert">{message(grantDocumentAcl.error)}</p>}
      {grantDocumentAcl.isSuccess && <p className="knowledge-success" role="status">文档授权已提交。</p>}
      {revokeDocumentAcl.isError && <p className="knowledge-error" role="alert">{message(revokeDocumentAcl.error)}</p>}
      {revokeDocumentAcl.isSuccess && <p className="knowledge-success" role="status">文档授权已撤销。</p>}
      {docGrants.length === 0 ? <p className="knowledge-empty">本次尚未提交文档授权。</p> : <ul className="knowledge-grant-list">{docGrants.map(grant => <li key={grant.principal_type + grant.principal_id + grant.permission}><span>{valueLabel(grant.principal_type)} · {grant.principal_id} · {grant.permission === 'manage' ? '管理' : '只读'}</span><button className="button-secondary" onClick={() => revokeDocumentAcl.mutate(grant)} disabled={revokeDocumentAcl.isPending}>撤销</button></li>)}</ul>}
    </section>
    <section className="knowledge-panel" aria-labelledby="knowledge-chunk-acl-title"><div className="knowledge-panel-heading"><h2 id="knowledge-chunk-acl-title">切片访问控制（ACL）</h2><span>按切片授权</span></div>
      <p className="knowledge-help">先在下拉框选择本文档的切片，再设置主体与权限。授权后切片可见性由服务端重新计算。</p>
      <form className="knowledge-acl-form" onSubmit={submitChunkAcl}>
        <label>切片<select value={chunkAclChunkId} onChange={e => setChunkAclChunkId(e.target.value)} required><option value="">选择切片</option>{chunks.map(c => <option key={c.chunk_id} value={c.chunk_id}>#{c.ordinal} · {c.chunk_id}</option>)}</select></label>
        <label>主体类型<select value={chunkPrincipalType} onChange={e => setChunkPrincipalType(e.target.value)}><option value="user">用户</option><option value="role">角色</option><option value="project">项目</option></select></label>
        <label>主体 ID<input value={chunkPrincipalId} onChange={e => setChunkPrincipalId(e.target.value)} placeholder="用户 / 角色 / 项目 ID" required /></label>
        <label>权限<select value={chunkPermission} onChange={e => setChunkPermission(e.target.value)}><option value="read">只读</option><option value="manage">管理</option></select></label>
        <div className="knowledge-acl-actions">
          <button className="button-primary" type="submit" disabled={grantChunkAcl.isPending || !chunkAclChunkId || !chunkPrincipalId.trim()}>{grantChunkAcl.isPending ? '提交中…' : '授予切片权限'}</button>
          <button className="button-secondary" type="button" onClick={() => { if (chunkAclChunkId && chunkPrincipalId.trim()) revokeChunkAcl.mutate({ chunk_id: chunkAclChunkId, principal_type: chunkPrincipalType, principal_id: chunkPrincipalId.trim(), permission: chunkPermission }) }} disabled={revokeChunkAcl.isPending || !chunkAclChunkId || !chunkPrincipalId.trim()}>{revokeChunkAcl.isPending ? '撤销中…' : '撤销此授权'}</button>
        </div>
      </form>
      {grantChunkAcl.isError && <p className="knowledge-error" role="alert">{message(grantChunkAcl.error)}</p>}
      {grantChunkAcl.isSuccess && <p className="knowledge-success" role="status">切片授权已提交。</p>}
      {revokeChunkAcl.isError && <p className="knowledge-error" role="alert">{message(revokeChunkAcl.error)}</p>}
      {revokeChunkAcl.isSuccess && <p className="knowledge-success" role="status">切片授权已撤销。</p>}
      {chunkGrants.length === 0 ? <p className="knowledge-empty">本次尚未提交切片授权。</p> : <ul className="knowledge-grant-list">{chunkGrants.map(grant => <li key={grant.chunk_id + grant.principal_type + grant.principal_id + grant.permission}><span>切片 {grant.chunk_id} · {valueLabel(grant.principal_type)} · {grant.principal_id} · {grant.permission === 'manage' ? '管理' : '只读'}</span><button className="button-secondary" onClick={() => revokeChunkAcl.mutate(grant)} disabled={revokeChunkAcl.isPending}>撤销</button></li>)}</ul>}
    </section>
  </>}</section>
}
