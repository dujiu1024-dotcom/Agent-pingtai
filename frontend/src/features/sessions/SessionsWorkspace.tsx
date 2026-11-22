import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { ErrorState, LoadingState } from '@/components/States'
import { ApiError, apiClient } from '@/lib/api'

import './sessions-workspace.css'

type Session = {
  session_id: string
  title: string
  project_id: string | null
  status: string
  created_at: string
  updated_at: string
}
type Message = {
  message_id: string
  role: 'user' | 'assistant' | 'system' | 'tool'
  content: string
  actor_id: string | null
  task_id: string | null
  run_id: string | null
  asset_ids: string[]
  created_at: string
}
type Asset = {
  asset_id: string
  filename: string
  media_type: string
  size_bytes: number
  status: string
  session_id: string | null
  version: number
  created_at: string
}
type Items<T> = { items: T[]; next_cursor?: string | null }

function formatDate(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}
function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value < 0) return '—'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / (1024 * 1024)).toFixed(1)} MB`
}
function errorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 401) return '当前会话未认证或已过期，请重新登录后访问。'
  return error instanceof Error ? error.message : '请求失败，请稍后重试。'
}

export function SessionsWorkspace() {
  const { sessionId } = useParams<{ sessionId: string }>()
  const [sessions, setSessions] = useState<Session[]>([])
  const [messages, setMessages] = useState<Message[]>([])
  const [assets, setAssets] = useState<Asset[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = useState<string | null>(null)

  const load = useCallback(async (showLoading = false) => {
    if (showLoading) setLoading(true)
    setError(null)
    try {
      const list = await apiClient.get<Items<Session>>('/platform/v1/sessions', {
        etag: false,
        timeoutMs: 12_000,
      })
      setSessions(Array.isArray(list.items) ? list.items : [])
      if (sessionId) {
        const [messageResult, assetResult] = await Promise.all([
          apiClient.get<Items<Message>>(`/platform/v1/sessions/${encodeURIComponent(sessionId)}/messages?limit=100`, { etag: false }),
          apiClient.get<Items<Asset>>(`/platform/v1/files?session_id=${encodeURIComponent(sessionId)}&limit=100`, { etag: false }),
        ])
        setMessages(Array.isArray(messageResult.items) ? messageResult.items : [])
        setAssets(Array.isArray(assetResult.items) ? assetResult.items : [])
      } else {
        setMessages([])
        setAssets([])
      }
      setLastUpdated(new Date().toISOString())
    } catch (reason) {
      setError(errorMessage(reason))
    } finally {
      setLoading(false)
    }
  }, [sessionId])

  useEffect(() => {
    void load(true)
    const timer = window.setInterval(() => void load(false), 15_000)
    return () => window.clearInterval(timer)
  }, [load])

  if (loading) return <div className="sessions-page"><LoadingState label="正在加载会话数据" /></div>
  if (error && sessions.length === 0) {
    return <div className="sessions-page"><ErrorState title="会话数据加载失败" description={error} onRetry={() => void load(true)} /></div>
  }

  const selected = sessions.find((session) => session.session_id === sessionId)
  return (
    <section className="page-content sessions-page">
      <div className="sessions-heading">
        <div>
          <p className="eyebrow">OPERATIONS / SESSIONS</p>
          <h1>会话与消息</h1>
          <p>查看当前身份有权访问的会话、消息时间线与文件资产。</p>
        </div>
        <div className="sessions-refresh">
          <span>{lastUpdated ? `更新于 ${formatDate(lastUpdated)}` : '尚未同步'}</span>
          <button type="button" onClick={() => void load(true)}>刷新</button>
        </div>
      </div>
      {error && <div className="sessions-inline-error" role="alert">{error}</div>}
      <div className="sessions-layout">
        <aside className="sessions-list" aria-label="会话列表">
          <div className="sessions-list-title"><strong>会话列表</strong><span>{sessions.length}</span></div>
          {sessions.length === 0 ? (
            <div className="sessions-empty">暂无可访问的会话</div>
          ) : sessions.map((session) => (
            <Link
              className={session.session_id === sessionId ? 'session-row selected' : 'session-row'}
              key={session.session_id}
              to={`/operations/sessions/${encodeURIComponent(session.session_id)}`}
            >
              <strong>{session.title || '未命名会话'}</strong>
              <span>{session.status === 'active' ? '进行中' : session.status}</span>
              <small>{formatDate(session.updated_at)}</small>
            </Link>
          ))}
        </aside>
        <div className="sessions-detail">
          {!sessionId ? (
            <div className="sessions-placeholder"><strong>选择一个会话</strong><span>会话详情、消息流和关联文件将在此显示。</span></div>
          ) : !selected ? (
            <div className="sessions-placeholder"><strong>会话不可用</strong><span>该会话不存在、已归档或当前身份无权访问。</span><Link to="/operations/sessions">返回会话列表</Link></div>
          ) : (
            <>
              <header className="session-detail-header">
                <div><p className="eyebrow">SESSION DETAIL</p><h2>{selected.title || '未命名会话'}</h2><span>{selected.session_id}</span></div>
                <span className={selected.status === 'active' ? 'session-status active' : 'session-status'}>{selected.status}</span>
              </header>
              <section className="session-panel">
                <div className="session-panel-title"><h3>消息时间线</h3><span>{messages.length} 条 · 最近 100 条</span></div>
                {messages.length === 0 ? <div className="sessions-empty">暂无消息</div> : (
                  <ol className="session-messages">
                    {messages.map((message) => (
                      <li key={message.message_id} className={`session-message role-${message.role}`}>
                        <div className="session-message-meta"><strong>{message.role === 'user' ? '用户' : message.role === 'assistant' ? 'Agent' : message.role}</strong><time>{formatDate(message.created_at)}</time></div>
                        <p>{message.content || '（无文本内容）'}</p>
                        {(message.task_id || message.run_id || message.asset_ids.length > 0) && (
                          <div className="session-message-links">
                            {message.task_id && <span>Task: {message.task_id}</span>}
                            {message.run_id && <span>Run: {message.run_id}</span>}
                            {message.asset_ids.map((id) => <span key={id}>File: {id}</span>)}
                          </div>
                        )}
                      </li>
                    ))}
                  </ol>
                )}
              </section>
              <section className="session-panel">
                <div className="session-panel-title"><h3>文件资产</h3><span>{assets.length} 项</span></div>
                {assets.length === 0 ? <div className="sessions-empty">此会话没有关联文件</div> : (
                  <div className="session-assets">
                    {assets.map((asset) => (
                      <div className="session-asset" key={asset.asset_id}>
                        <div><strong>{asset.filename}</strong><small>{asset.media_type} · {formatBytes(asset.size_bytes)} · v{asset.version}</small></div>
                        <span>{asset.status}</span>
                        <a href={`/api/platform/v1/files/${encodeURIComponent(asset.asset_id)}/versions/${asset.version}`} target="_blank" rel="noreferrer">下载</a>
                      </div>
                    ))}
                  </div>
                )}
              </section>
            </>
          )}
        </div>
      </div>
    </section>
  )
}
