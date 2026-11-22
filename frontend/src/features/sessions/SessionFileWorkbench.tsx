import { useEffect, useMemo, useState } from 'react'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { ApiError, apiClient } from '@/lib/api'
import { EmptyState, ErrorState, LoadingState, ModeRestrictedState } from '@/components/States'
import './session-file-workbench.css'

type SessionRecord = {
  session_id: string
  title: string | null
  project_id: string | null
  status: string
  created_at: string
  updated_at: string
}
type SessionList = { items: SessionRecord[] }
type SessionDetail = {
  session: SessionRecord & { agent_id?: string | null; isolation_scope?: string }
}
type SessionMessage = {
  message_id: string
  role: string
  content: string
  actor_id?: string | null
  task_id?: string | null
  run_id?: string | null
  asset_ids: string[]
  metadata: Record<string, unknown>
  created_at: string
}
type MessagePage = { items: SessionMessage[]; next_cursor?: string | null }
type FileAsset = {
  asset_id: string
  filename: string
  media_type: string
  size_bytes: number
  status: string
  session_id: string | null
  project_id?: string | null
  version: number
  created_at: string
  owner_id?: string | null
  artifact_type?: string | null
  task_id?: string | null
  run_id?: string | null
}
type FileList = { items: FileAsset[] }
type FileMetadata = FileAsset & { sha256: string }
type FileVersion = {
  version: number
  filename: string
  media_type: string
  size_bytes: number
  sha256: string
  status: string
  created_at: string
}
type FileVersions = { asset_id: string; current_version: number; items: FileVersion[] }

const API = '/platform/v1'
const dateLabel = (value?: string) => {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}
const byteLabel = (value: number) => {
  if (!Number.isFinite(value) || value < 0) return '—'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}
const errorLabel = (error: unknown) => {
  if (error instanceof ApiError) {
    if (error.status === 401) return '身份认证缺失或已过期。'
    if (error.status === 403) return '当前身份无权访问此会话或文件。'
    if (error.status === 404) return '资源不存在，或当前身份不可见。'
    if (error.status === 410) return '文件已删除，不再提供下载。'
  }
  return error instanceof Error ? error.message : '请求失败，请重试。'
}
const queryOptions = { staleTime: 0, gcTime: 0, refetchOnWindowFocus: true }
const safeFileName = (value: string) => value.replace(/[\\r\\n"]/g, '_')

async function downloadVersion(assetId: string, version: number, filename: string) {
  const blob = await apiClient.get<Blob>(
    `${API}/files/${encodeURIComponent(assetId)}/versions/${version}`,
    { responseType: 'blob', etag: false, cache: 'no-store', headers: { Accept: '*/*' } },
  ).catch((error: unknown) => {
    if (error instanceof ApiError && [401, 403, 404, 410].includes(error.status)) {
      throw new Error(errorLabel(new ApiError('文件下载被拒绝', { status: error.status, code: 'file_download_denied' })))
    }
    throw error
  })
  const objectUrl = URL.createObjectURL(blob)
  try {
    const anchor = document.createElement('a')
    anchor.href = objectUrl
    anchor.download = safeFileName(filename)
    anchor.rel = 'noopener'
    document.body.append(anchor)
    anchor.click()
    anchor.remove()
  } finally {
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000)
  }
}

export function SessionFileWorkbench({ view, authEnabled = true }: { view: 'sessions' | 'files'; authEnabled?: boolean }) {
  const { conversationId, sessionId: legacySessionId, fileId } = useParams()
  const selectedSessionId = conversationId ?? legacySessionId
  if (view === 'sessions') {
    return selectedSessionId
      ? <SessionDetail sessionId={selectedSessionId} />
      : <SessionList />
  }
  if (!authEnabled) return <main className="page-content sf-page"><ModeRestrictedState title="文件资产仅在权限模式开放" description="当前服务端 auth.enabled=false。文件列表、预览和下载依赖身份与资源授权；免登录模式下不会请求文件 API。" /></main>
  return fileId ? <FileDetail assetId={fileId} /> : <FileList />
}

function SessionList() {
  const [filter, setFilter] = useState('')
  const [includeArchived, setIncludeArchived] = useState(false)
  const sessionsQuery = useQuery({
    queryKey: ['session-workbench', 'sessions', includeArchived],
    queryFn: () => apiClient.get<SessionList>(
      `${API}/sessions?limit=100&include_archived=${includeArchived}`,
      { etag: false, cache: 'no-store' },
    ),
    refetchInterval: 15000,
    ...queryOptions,
  })
  const sessions = sessionsQuery.data?.items ?? []
  const visible = useMemo(() => {
    const q = filter.trim().toLocaleLowerCase()
    return sessions.filter((session) =>
      !q || [session.session_id, session.title ?? '', session.project_id ?? '', session.status]
        .some((value) => value.toLocaleLowerCase().includes(q)),
    )
  }, [filter, sessions])

  if (sessionsQuery.isPending) return <main className="page-content sf-page"><LoadingState label="正在加载会话" /></main>
  if (sessionsQuery.error) return <main className="page-content sf-page"><ErrorState title="会话列表加载失败" description={errorLabel(sessionsQuery.error)} onRetry={() => void sessionsQuery.refetch()} /></main>

  return (
    <main className="page-content sf-page">
      <header className="sf-heading">
        <div><p className="eyebrow">OPERATIONS / SESSIONS</p><h1>会话与消息</h1><p>浏览当前身份有权访问的会话、消息时间线和关联文件。</p></div>
        <span className="sf-count">{visible.length} / {sessions.length} 个会话</span>
      </header>
      <div className="sf-toolbar">
        <label>搜索会话<input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="会话 ID、标题、项目或状态" /></label>
        <label className="sf-check"><input type="checkbox" checked={includeArchived} onChange={(event) => setIncludeArchived(event.target.checked)} /> 包含已归档会话</label>
        <button type="button" className="sf-secondary" onClick={() => void sessionsQuery.refetch()}>刷新</button>
      </div>
      {visible.length === 0
        ? <EmptyState title="没有可显示的会话" description={sessions.length ? '没有符合筛选条件的会话。' : '服务端没有返回当前身份可访问的会话。'} />
        : <div className="sf-session-grid">{visible.map((session) => (
          <Link className="sf-session-card" key={session.session_id} to={`/operations/conversations/${encodeURIComponent(session.session_id)}`}>
            <span className={`sf-status sf-status--${session.status}`}>{session.status}</span>
            <strong>{session.title?.trim() || '未命名会话'}</strong>
            <small>{session.session_id}</small>
            <span>项目：{session.project_id || '未关联'}</span>
            <time>{dateLabel(session.updated_at)}</time>
          </Link>
        ))}</div>}
      <p className="sf-footnote">列表由服务端按 Principal、租户和会话/项目授权过滤；前端不自行扩大可见范围。每 15 秒重新读取会话状态。</p>
    </main>
  )
}

function SessionDetail({ sessionId }: { sessionId: string }) {
  const sessionQuery = useQuery({
    queryKey: ['session-workbench', 'detail', sessionId],
    queryFn: () => apiClient.get<SessionDetail>(
      `${API}/sessions/${encodeURIComponent(sessionId)}?message_limit=1`,
      { etag: false, cache: 'no-store' },
    ),
    refetchInterval: 15000,
    ...queryOptions,
  })
  const messagesQuery = useInfiniteQuery({
    queryKey: ['session-workbench', 'messages', sessionId],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => {
      const cursor = pageParam ? `&before_message_id=${encodeURIComponent(pageParam)}` : ''
      return apiClient.get<MessagePage>(
        `${API}/sessions/${encodeURIComponent(sessionId)}/messages?limit=100${cursor}`,
        { etag: false, cache: 'no-store' },
      )
    },
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    refetchInterval: 15000,
    ...queryOptions,
  })
  const filesQuery = useQuery({
    queryKey: ['session-workbench', 'files', sessionId],
    queryFn: () => apiClient.get<FileList>(
      `${API}/files?session_id=${encodeURIComponent(sessionId)}&limit=100`,
      { etag: false, cache: 'no-store' },
    ),
    ...queryOptions,
  })
  const session = sessionQuery.data?.session
  const messages = (messagesQuery.data?.pages ?? []).flatMap((page) => page.items).sort((a, b) => a.created_at.localeCompare(b.created_at) || a.message_id.localeCompare(b.message_id))
  const files = filesQuery.data?.items ?? []

  if (sessionQuery.isPending || messagesQuery.isPending) return <main className="page-content sf-page"><LoadingState label="正在加载会话详情与消息" /></main>
  if (sessionQuery.error) return <main className="page-content sf-page"><ErrorState title="会话详情不可用" description={errorLabel(sessionQuery.error)} /></main>
  if (messagesQuery.error) return <main className="page-content sf-page"><ErrorState title="消息时间线加载失败" description={errorLabel(messagesQuery.error)} onRetry={() => void messagesQuery.refetch()} /></main>
  if (!session) return <main className="page-content sf-page"><EmptyState title="会话不存在" description="会话可能已删除，或当前身份无权访问。" /></main>

  return (
    <main className="page-content sf-page">
      <header className="sf-heading">
        <div><p className="eyebrow">SESSION / DETAIL</p><h1>{session.title?.trim() || '未命名会话'}</h1><p>{session.session_id} · {session.status} · 项目 {session.project_id || '未关联'}</p></div>
        <Link className="sf-secondary sf-link-button" to="/operations/conversations">返回会话列表</Link>
      </header>
      <div className="sf-detail-grid">
        <section className="sf-panel sf-timeline">
          <div className="sf-panel-heading"><div><h2>消息时间线</h2><span>按服务端游标加载历史消息 · 自动刷新</span></div><button type="button" className="sf-secondary" onClick={() => void messagesQuery.refetch()}>刷新</button></div>
          {messagesQuery.hasNextPage && <button type="button" className="sf-load-older" disabled={messagesQuery.isFetchingNextPage} onClick={() => void messagesQuery.fetchNextPage()}>{messagesQuery.isFetchingNextPage ? '正在加载…' : '加载更早消息'}</button>}
          {messages.length === 0 ? <EmptyState title="暂无消息" description="该会话尚无可访问的消息记录。" /> : <ol className="sf-message-list">{messages.map((message) => (
            <li className={`sf-message sf-message--${message.role}`} key={message.message_id}>
              <div className="sf-message-head"><strong>{message.role}</strong><time>{dateLabel(message.created_at)}</time></div>
              <p className="sf-message-content">{message.content || (message.metadata?.tool_call ? 'Tool call' : '（空消息）')}</p>
              {message.metadata && Object.keys(message.metadata).length > 0 && <details className="sf-message-meta"><summary>Tool / 消息元数据</summary><pre>{JSON.stringify(message.metadata, null, 2)}</pre></details>}
              <div className="sf-message-links">
                {message.task_id && <Link to={`/operations/tasks/${encodeURIComponent(message.task_id)}`}>Task {message.task_id}</Link>}
                {message.run_id && <Link to={`/operations/runs/${encodeURIComponent(message.run_id)}`}>Run {message.run_id}</Link>}
                {message.asset_ids.map((assetId) => <Link key={assetId} to={`/operations/files/${encodeURIComponent(assetId)}`}>附件 {assetId}</Link>)}
              </div>
            </li>
          ))}</ol>}
        </section>
        <aside className="sf-panel sf-related">
          <div className="sf-panel-heading"><div><h2>关联文件</h2><span>{files.length} 个资产</span></div><Link to="/operations/files">全部文件</Link></div>
          {filesQuery.error ? <p className="sf-inline-error">{errorLabel(filesQuery.error)}</p> : files.length === 0 ? <p className="sf-muted">当前会话没有可访问的文件。</p> : <ul className="sf-file-list">{files.map((file) => <li key={file.asset_id}><Link to={`/operations/files/${encodeURIComponent(file.asset_id)}`}>{file.filename}</Link><small>{file.media_type} · {byteLabel(file.size_bytes)} · v{file.version}</small></li>)}</ul>}
        </aside>
      </div>
      <p className="sf-footnote">消息内容不写入 localStorage/sessionStorage；查询关闭 ETag 缓存并使用 no-store。刷新采用服务端轮询，不伪称 SSE 实时推送。所有会话、消息及附件访问由服务端再次授权。</p>
    </main>
  )
}

function FileList() {
  const [filter, setFilter] = useState('')
  const filesQuery = useQuery({
    queryKey: ['session-workbench', 'files', 'all'],
    queryFn: () => apiClient.get<FileList>(`${API}/files?limit=100&all_visible=true`, { etag: false, cache: 'no-store' }),
    refetchInterval: 30000,
    ...queryOptions,
  })
  const files = filesQuery.data?.items ?? []
  const visible = useMemo(() => {
    const q = filter.trim().toLocaleLowerCase()
    return files.filter((file) => !q || [
      file.filename, file.asset_id, file.session_id ?? '', file.project_id ?? '', file.media_type,
    ].some((value) => value.toLocaleLowerCase().includes(q)))
  }, [files, filter])
  if (filesQuery.isPending) return <main className="page-content sf-page"><LoadingState label="正在加载文件与产物" /></main>
  if (filesQuery.error) return <main className="page-content sf-page"><ErrorState title="文件列表加载失败" description={errorLabel(filesQuery.error)} onRetry={() => void filesQuery.refetch()} /></main>
  return (
    <main className="page-content sf-page">
      <header className="sf-heading"><div><p className="eyebrow">OPERATIONS / FILES</p><h1>文件与产物</h1><p>展示当前身份经服务端授权可访问的文件元数据与版本。</p></div><span className="sf-count">{visible.length} / {files.length} 个文件</span></header>
      <div className="sf-toolbar"><label>搜索文件<input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="文件名、资产 ID、会话或项目" /></label><button className="sf-secondary" type="button" onClick={() => void filesQuery.refetch()}>刷新</button></div>
      {visible.length === 0 ? <EmptyState title="暂无文件或产物" description={files.length ? '没有符合筛选条件的文件。' : '服务端没有返回当前身份可访问的文件。'} /> : <div className="sf-table-wrap"><table className="sf-table"><thead><tr><th>文件名</th><th>类型</th><th>大小</th><th>来源会话</th><th>版本 / 状态</th><th>产物关联</th><th>上传时间</th><th>操作</th></tr></thead><tbody>{visible.map((file) => <tr key={file.asset_id}><td><strong>{file.filename}</strong><small>{file.asset_id}</small></td><td>{file.media_type}</td><td>{byteLabel(file.size_bytes)}</td><td>{file.session_id ? <Link to={`/operations/conversations/${encodeURIComponent(file.session_id)}`}>{file.session_id}</Link> : file.project_id || '—'}</td><td>{file.status} · v{file.version}</td><td>{file.artifact_type || '—'}{file.task_id && <> · <Link to={`/operations/tasks/${encodeURIComponent(file.task_id)}`}>Task</Link></>}{file.run_id && <> · <Link to={`/operations/runs/${encodeURIComponent(file.run_id)}`}>Run</Link></>}</td><td>{dateLabel(file.created_at)}</td><td><Link to={`/operations/files/${encodeURIComponent(file.asset_id)}`}>详情 / 下载</Link></td></tr>)}</tbody></table></div>}
      <p className="sf-footnote">仅显示服务端返回的前 100 条。服务端逐项执行 file、session、project 授权并记录访问审计。</p>
    </main>
  )
}

function FileDetail({ assetId }: { assetId: string }) {
  const metadataQuery = useQuery({
    queryKey: ['session-workbench', 'file', assetId],
    queryFn: () => apiClient.get<FileMetadata>(`${API}/files/${encodeURIComponent(assetId)}/metadata`, { etag: false, cache: 'no-store' }),
    ...queryOptions,
  })
  const versionsQuery = useQuery({
    queryKey: ['session-workbench', 'file-versions', assetId],
    queryFn: () => apiClient.get<FileVersions>(`${API}/files/${encodeURIComponent(assetId)}/versions`, { etag: false, cache: 'no-store' }),
    ...queryOptions,
  })
  const [downloadError, setDownloadError] = useState('')
  const [downloading, setDownloading] = useState<number | null>(null)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [previewError, setPreviewError] = useState('')
  const metadata = metadataQuery.data
  const previewType = metadata?.media_type?.toLowerCase() ?? ''
  const previewable = previewType.startsWith('image/') || previewType.startsWith('video/') || previewType.startsWith('audio/') || previewType === 'application/pdf'
  useEffect(() => {
    let active = true
    let objectUrl: string | null = null
    setPreviewUrl(null)
    setPreviewError('')
    if (!metadata || !previewable) return () => { active = false }
    void fetch(`/api${API}/files/${encodeURIComponent(assetId)}/versions/${metadata.version}`, {
      credentials: 'same-origin', cache: 'no-store', headers: { Accept: metadata.media_type },
    }).then(async (response) => {
      if (!response.ok) throw new Error(errorLabel(new ApiError('文件预览被拒绝', { status: response.status, code: 'file_preview_denied' })))
      const blob = await response.blob()
      if (active) {
        objectUrl = URL.createObjectURL(blob)
        setPreviewUrl(objectUrl)
      }
    }).catch((error: unknown) => { if (active) setPreviewError(errorLabel(error)) })
    return () => {
      active = false
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [assetId, metadata?.media_type, metadata?.version, previewable])
  const download = async (version: FileVersion) => {
    setDownloading(version.version)
    setDownloadError('')
    try { await downloadVersion(assetId, version.version, version.filename) }
    catch (error) { setDownloadError(errorLabel(error)) }
    finally { setDownloading(null) }
  }
  if (metadataQuery.isPending || versionsQuery.isPending) return <main className="page-content sf-page"><LoadingState label="正在加载文件元数据与版本" /></main>
  if (metadataQuery.error || versionsQuery.error) return <main className="page-content sf-page"><ErrorState title="文件详情不可用" description={errorLabel(metadataQuery.error ?? versionsQuery.error)} /></main>
  if (!metadata) return <main className="page-content sf-page"><EmptyState title="文件不存在" description="文件可能已删除，或当前身份无权访问。" /></main>
  return (
    <main className="page-content sf-page">
      <header className="sf-heading"><div><p className="eyebrow">FILE / ARTIFACT</p><h1>{metadata.filename}</h1><p>{metadata.asset_id} · {metadata.media_type} · {byteLabel(metadata.size_bytes)}</p></div><Link className="sf-secondary sf-link-button" to="/operations/files">返回文件列表</Link></header>
      <section className="sf-panel sf-file-detail"><h2>资产元数据</h2><dl><dt>状态</dt><dd>{metadata.status}</dd><dt>当前版本</dt><dd>v{metadata.version}</dd><dt>SHA-256</dt><dd className="sf-digest">{metadata.sha256}</dd><dt>来源会话</dt><dd>{metadata.session_id ? <Link to={`/operations/conversations/${encodeURIComponent(metadata.session_id)}`}>{metadata.session_id}</Link> : '—'}</dd><dt>产物类型</dt><dd>{metadata.artifact_type || '—'}</dd><dt>关联 Task</dt><dd>{metadata.task_id ? <Link to={`/operations/tasks/${encodeURIComponent(metadata.task_id)}`}>{metadata.task_id}</Link> : '—'}</dd><dt>关联 Run</dt><dd>{metadata.run_id ? <Link to={`/operations/runs/${encodeURIComponent(metadata.run_id)}`}>{metadata.run_id}</Link> : '—'}</dd><dt>创建时间</dt><dd>{dateLabel(metadata.created_at)}</dd></dl></section>
      {previewable && <section className="sf-panel sf-file-preview"><h2>文件预览</h2>{previewError ? <p className="sf-inline-error" role="alert">{previewError}</p> : !previewUrl ? <p className="sf-muted">正在加载受保护的文件预览…</p> : previewType.startsWith('image/') ? <img src={previewUrl} alt={metadata.filename} /> : previewType.startsWith('video/') ? <video src={previewUrl} controls preload="metadata" /> : previewType.startsWith('audio/') ? <audio src={previewUrl} controls preload="metadata" /> : <iframe src={previewUrl} title={`PDF 预览：${metadata.filename}`} />}</section>}
      <section className="sf-panel"><h2>文件版本</h2>{downloadError && <p className="sf-inline-error" role="alert">{downloadError}</p>}<div className="sf-table-wrap"><table className="sf-table"><thead><tr><th>版本</th><th>文件名</th><th>类型</th><th>大小</th><th>SHA-256</th><th>创建时间</th><th>下载</th></tr></thead><tbody>{(versionsQuery.data?.items ?? []).map((version) => <tr key={version.version}><td>v{version.version}{version.version === versionsQuery.data?.current_version ? ' · 当前' : ''}</td><td>{version.filename}</td><td>{version.media_type}</td><td>{byteLabel(version.size_bytes)}</td><td className="sf-digest">{version.sha256}</td><td>{dateLabel(version.created_at)}</td><td><button className="sf-download" type="button" disabled={downloading !== null} onClick={() => void download(version)}>{downloading === version.version ? '下载中…' : '下载'}</button></td></tr>)}</tbody></table></div></section>
      <p className="sf-footnote">下载通过 same-origin credentials 请求受保护的版本端点；不使用预签名 URL、不缓存文件内容，服务端在每次下载时重新检查 file/session/project 权限并记录审计。</p>
    </main>
  )
}
