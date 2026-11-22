import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { ErrorState, LoadingState } from '@/components/States'
import { ApiError, apiClient } from '@/lib/api'

import './sessions-workspace.css'

type Session = {
  session_id: string
  title?: string | null
  project_id?: string | null
  status?: string | null
  created_at?: string | null
  updated_at?: string | null
}
type Message = {
  message_id: string
  role?: string
  content?: string | null
  actor_id?: string | null
  task_id?: string | null
  run_id?: string | null
  asset_ids?: string[]
  created_at?: string | null
}
type Asset = {
  asset_id: string
  filename?: string | null
  media_type?: string | null
  size_bytes?: number | null
  status?: string | null
  session_id?: string | null
  version?: number | null
  created_at?: string | null
}
type Items<T> = { items?: T[]; data?: T[]; next_cursor?: string | null }

function rows<T>(value: Items<T> | T[] | null | undefined): T[] {
  if (Array.isArray(value)) return value
  if (value && Array.isArray(value.items)) return value.items
  if (value && Array.isArray(value.data)) return value.data
  return []
}
function formatDate(value?: string | null): string {
  if (!value) return '时间未知'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}
function formatBytes(value?: number | null): string {
  if (value == null || !Number.isFinite(value) || value < 0) return '大小未知'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / (1024 * 1024)).toFixed(1)} MB`
}
function describeError(error: unknown): string {
  if (error instanceof ApiError && error.status === 401) return '当前身份未认证或已过期，请重新登录后访问。'
  if (error instanceof ApiError && error.status === 403) return '当前身份无权访问该会话或文件。'
  return error instanceof Error ? error.message : '请求失败，请稍后重试。'
}

export function SessionsWorkspace() {
  const { sessionId } = useParams<{ sessionId?: string }>()
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
      const list = await apiClient.get<Items<Session> | Session[]>('/platform/v1/sessions', {
        etag: false,
        timeoutMs: 12_000,
      })
      const sessionRows = rows(list)
      setSessions(sessionRows)
      if (sessionId) {
        const encodedId = encodeURIComponent(sessionId)
        const [messageResult, assetResult] = await Promise.all([
          apiClient.get<Items<Message> | Message[]>(`/platform/v1/sessions/${encodedId}/messages?limit=100`, { etag: false, timeoutMs: 12_000 }),
          apiClient.get<Items<Asset> | Asset[]>(`/platform/v1/files?session_id=${encodedId}&limit=100`, { etag: false, timeoutMs: 12_000 }),
        ])
        setMessages(rows(messageResult))
        setAssets(rows(assetResult))
      } else {
        setMessages([])
        setAssets([])
      }
      setLastUpdated(new Date().toISOString())
    } catch (reason) {
      setError(describeError(reason))
    } finally {
      setLoading(false)
    }
  }, [sessionId])

  useEffect(() => {
    let active = true
    const refresh = async (showLoading: boolean) => {
      if (!active) return
      await load(showLoading)
    }
    void refresh(true)
    const timer = window.setInterval(() => void refresh(false), 15_000)
    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [load])

  const selected = useMemo(
    () => sessions.find((session) => session.session_id === sessionId),
    [sessions, sessionId],
  )

  if (loading) return <div className="sessions-page"><LoadingState label="正在加载会话数据" /></div>
  if (error && sessions.length === 0) {
    return <div className="sessions-page"><ErrorState title="会话数据加载失败" description={error} onRetry={() => void load(true)} /></div>
  }

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
              <span>{session.status === 'active' ? '进行中' : session.status || '状态未知'}</span>
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
                <span className={selected.status === 'active' ? 'session-status active' : 'session-status'}>{selected.status || '状态未知'}</span>
              </header>
              <section className="session-panel">
                <div className="session-panel-title"><h3>消息时间线</h3><span>{messages.length} 条 · 最近 100 条 · 15 秒轮询</span></div>
                {messages.length === 0 ? <div className="sessions-empty">暂无消息</div> : (
                  <ol className="session-messages">
                    {messages.map((message) => (
                      <li key={message.message_id} className={`session-message role-${message.role || 'unknown'}`}>
                        <div className="session-message-meta"><strong>{message.role === 'user' ? '用户' : message.role === 'assistant' ? 'Agent' : message.role || '系统'}</strong><time>{formatDate(message.created_at)}</time></div>
                        <p>{message.content || '（无文本内容）'}</p>
                        {(message.task_id || message.run_id || (message.asset_ids?.length ?? 0) > 0) && (
                          <div className="session-message-links">
                            {message.task_id && <span>Task: {message.task_id}</span>}
                            {message.run_id && <span>Run: {message.run_id}</span>}
                            {(message.asset_ids || []).map((id) => <span key={id}>File: {id}</span>)}
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
                        <div><strong>{asset.filename || asset.asset_id}</strong><small>{asset.media_type || '类型未知'} · {formatBytes(asset.size_bytes)} · {asset.version ? `v${asset.version}` : '版本未知'}</small></div>
                        <span>{asset.status || '状态未知'}</span>
                        <a href={`/api/platform/v1/files/${encodeURIComponent(asset.asset_id)}/versions/${asset.version || 1}`} target="_blank" rel="noreferrer">下载</a>
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
