import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { ApiError, apiClient } from '@/lib/api'
import { ErrorState, LoadingState, ModeRestrictedState } from '@/components/States'

import './files-workspace.css'

type FileAsset = {
  asset_id: string
  filename: string
  media_type: string
  size_bytes: number
  status: string
  session_id: string | null
  version: number
  created_at: string
}
type FileResponse = { items: FileAsset[]; next_cursor?: string | null }

function formatDate(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}
function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value < 0) return '—'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  if (value < 1024 * 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`
  return `${(value / (1024 * 1024 * 1024)).toFixed(1)} GB`
}
function describeError(error: unknown): string {
  if (error instanceof ApiError && error.status === 401) return '当前会话未认证或已过期，请重新登录后访问文件资产。'
  if (error instanceof ApiError && error.status === 403) return '当前身份没有访问这些文件资产的权限。'
  if (error instanceof ApiError && error.status === 404) return '文件列表接口或目标资源不存在。'
  return error instanceof Error ? error.message : '文件资产加载失败，请稍后重试。'
}

export function FilesWorkspace({ authEnabled }: { authEnabled: boolean }) {
  const [items, setItems] = useState<FileAsset[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [typeFilter, setTypeFilter] = useState('all')
  const [lastUpdated, setLastUpdated] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const response = await apiClient.get<FileResponse>('platform/v1/files?limit=100', {
        etag: false,
        timeoutMs: 15000,
      })
      setItems(Array.isArray(response.items) ? response.items : [])
      setLastUpdated(new Date().toISOString())
    } catch (reason) {
      setError(describeError(reason))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (authEnabled) void load()
    else setLoading(false)
  }, [authEnabled, load])

  const types = useMemo(() => {
    const values = new Set(items.map((item) => item.media_type || '未知类型'))
    return [...values].sort((a, b) => a.localeCompare(b))
  }, [items])
  const visibleItems = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase()
    return items.filter((item) => {
      const matchesQuery = !normalized || [
        item.filename, item.asset_id, item.session_id ?? '', item.media_type,
      ].some((value) => value.toLocaleLowerCase().includes(normalized))
      const matchesType = typeFilter === 'all' || (item.media_type || '未知类型') === typeFilter
      return matchesQuery && matchesType
    })
  }, [items, query, typeFilter])

  if (!authEnabled) {
    return <section className="page-content files-page">
      <ModeRestrictedState title="文件资产仅在权限模式开放" description="当前服务端 auth.enabled=false。文件列表和下载依赖身份与资源授权，免登录模式下不请求或暴露文件资产。" />
    </section>
  }
  if (loading) return <section className="page-content files-page"><LoadingState label="正在加载文件资产" /></section>
  if (error && items.length === 0) {
    return <section className="page-content files-page">
      <ErrorState title="文件资产加载失败" description={error} onRetry={() => void load()} />
    </section>
  }

  return <section className="page-content files-page">
    <header className="files-heading">
      <div><p className="eyebrow">OPERATIONS / FILES</p><h1>文件与产物</h1><p>查看当前身份有权访问的文件资产，并按版本下载原始文件。</p></div>
      <div className="files-refresh"><span>{lastUpdated ? `更新于 ${formatDate(lastUpdated)}` : '尚未同步'}</span><button className="button-secondary" type="button" onClick={() => void load()}>刷新</button></div>
    </header>
    {error && <div className="files-alert" role="alert">{error}</div>}
    <div className="files-summary">
      <article><span>可访问文件</span><strong>{items.length}</strong></article>
      <article><span>筛选结果</span><strong>{visibleItems.length}</strong></article>
      <article><span>文件类型</span><strong>{types.length}</strong></article>
    </div>
    <section className="files-panel">
      <div className="files-toolbar">
        <label>搜索文件<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="文件名、资产 ID 或会话 ID" /></label>
        <label>类型筛选<select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}><option value="all">全部类型</option>{types.map((type) => <option value={type} key={type}>{type}</option>)}</select></label>
      </div>
      {visibleItems.length === 0 ? <div className="files-empty">{items.length === 0 ? '暂无可访问的文件资产。文件会在会话或任务产生附件后显示。' : '没有符合当前筛选条件的文件。'}</div> : (
        <div className="files-table-wrap"><table className="files-table">
          <thead><tr><th>文件</th><th>类型</th><th>大小</th><th>上传时间</th><th>来源会话</th><th>版本 / 状态</th><th>操作</th></tr></thead>
          <tbody>{visibleItems.map((item) => <tr key={item.asset_id}>
            <td><strong>{item.filename || '未命名文件'}</strong><small>{item.asset_id}</small></td>
            <td>{item.media_type || '未知类型'}</td>
            <td>{formatBytes(item.size_bytes)}</td>
            <td>{formatDate(item.created_at)}</td>
            <td>{item.session_id ? <Link to={`/operations/sessions/${encodeURIComponent(item.session_id)}`}>{item.session_id}</Link> : '—'}</td>
            <td><span className="files-status">{item.status || '未知'}</span><small>v{item.version}</small></td>
            <td><a className="files-download" href={`/api/platform/v1/files/${encodeURIComponent(item.asset_id)}/versions/${item.version}`} target="_blank" rel="noreferrer">下载</a></td>
          </tr>)}</tbody>
        </table></div>
      )}
      <p className="files-footnote">当前列表按 API 返回的前 100 条展示；下载由后端执行身份、租户和资源权限校验。</p>
    </section>
  </section>
}
