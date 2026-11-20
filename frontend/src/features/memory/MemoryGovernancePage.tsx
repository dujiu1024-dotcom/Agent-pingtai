import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ApiError, apiClient } from '@/lib/api'
import './memory-governance.css'

type Envelope<T> = { items: T[] }
type MemoryRecord = {
  memory_id: string
  scope: string
  scope_id: string
  content: string
  source_ref: string
  confidence: number
  version: number
  status: string
  expires_at?: string | null
  created_by?: string
  confirmed_by?: string | null
  recorded_at?: string
  metadata?: Record<string, unknown>
  conflict_key?: string | null
}
export type MemoryTab = 'all' | 'candidate' | 'active' | 'archived' | 'expired' | 'rejected'

const basePath = 'platform/v1/memories'
const errorMessage = (error: unknown) => {
  if (error instanceof ApiError && error.status === 401) return '服务端要求身份认证，请检查当前登录状态。'
  if (error instanceof ApiError && error.status === 403) return '当前身份无权查看或操作此记忆。'
  if (error instanceof ApiError && error.status === 404) return '记忆不存在，或当前身份不可见。'
  return error instanceof Error ? error.message : '请求失败，请稍后重试。'
}

export function matchesMemoryTab(
  item: MemoryRecord,
  tab: MemoryTab,
  now = Date.now(),
): boolean {
  if (tab === 'all') return true
  if (tab === 'candidate') return item.status === 'candidate' || item.status === 'conflict'
  if (tab === 'expired') {
    return Boolean(item.expires_at && Date.parse(item.expires_at) <= now)
  }
  return item.status === tab
}

export function MemoryGovernancePage() {
  const { memoryId } = useParams()
  return memoryId ? <MemoryDetail memoryId={memoryId} /> : <MemoryList />
}

function MemoryList() {
  const [items, setItems] = useState<MemoryRecord[]>([])
  const [tab, setTab] = useState<MemoryTab>('all')
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [busyId, setBusyId] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [active, candidates] = await Promise.all([
        apiClient.get<Envelope<MemoryRecord>>(basePath + '?limit=100'),
        apiClient.get<Envelope<MemoryRecord>>(basePath + '/candidates'),
      ])
      const merged = new Map<string, MemoryRecord>()
      for (const item of active.items ?? []) merged.set(item.memory_id, item)
      for (const item of candidates.items ?? []) merged.set(item.memory_id, item)
      setItems([...merged.values()])
    } catch (reason) {
      setError(errorMessage(reason))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const visibleItems = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase()
    return items.filter(item => matchesMemoryTab(item, tab) && (
      !normalized ||
      item.content.toLocaleLowerCase().includes(normalized) ||
      item.memory_id.toLocaleLowerCase().includes(normalized) ||
      item.scope.toLocaleLowerCase().includes(normalized) ||
      item.source_ref.toLocaleLowerCase().includes(normalized)
    ))
  }, [items, query, tab])

  const confirm = async (item: MemoryRecord) => {
    setBusyId(item.memory_id)
    setError('')
    setNotice('')
    try {
      await apiClient.post(basePath + '/' + encodeURIComponent(item.memory_id) + '/confirm')
      setNotice('记忆已确认，列表已刷新。')
      await load()
    } catch (reason) {
      setError(errorMessage(reason))
    } finally {
      setBusyId('')
    }
  }

  const archive = async (item: MemoryRecord) => {
    if (!window.confirm('归档后该记忆将不再作为当前有效记忆使用。继续？')) return
    setBusyId(item.memory_id)
    setError('')
    setNotice('')
    try {
      await apiClient.post(basePath + '/' + encodeURIComponent(item.memory_id) + '/archive')
      setNotice('记忆已归档。')
      await load()
    } catch (reason) {
      setError(errorMessage(reason))
    } finally {
      setBusyId('')
    }
  }

  const tabs: { id: MemoryTab; label: string }[] = [
    { id: 'all', label: '全部' },
    { id: 'candidate', label: '待确认' },
    { id: 'active', label: '已生效' },
    { id: 'archived', label: '已归档' },
    { id: 'expired', label: '已过期' },
    { id: 'rejected', label: '已拒绝' },
  ]

  return <section className="page-content memory-page">
    <header className="memory-heading">
      <div><p className="eyebrow">P-UI-002-W1-2 · MEMORY</p><h1>记忆治理</h1><p>查看候选记忆、确认状态、来源依据与版本历史。</p></div>
      <button className="memory-button secondary" type="button" onClick={() => void load()} disabled={loading}>刷新</button>
    </header>
    <div className="memory-summary">
      <article><span>当前可见</span><strong>{loading ? '—' : items.length}</strong></article>
      <article><span>待确认</span><strong>{loading ? '—' : items.filter(item => matchesMemoryTab(item, 'candidate')).length}</strong></article>
      <article><span>已生效</span><strong>{loading ? '—' : items.filter(item => item.status === 'active').length}</strong></article>
    </div>
    {error && <div className="memory-alert" role="alert">{error}</div>}
    {notice && <div className="memory-notice" role="status">{notice}</div>}
    <section className="memory-panel">
      <div className="memory-toolbar">
        <div className="memory-tabs" role="tablist" aria-label="记忆状态筛选">
          {tabs.map(item => <button key={item.id} type="button" role="tab" aria-selected={tab === item.id} className={tab === item.id ? 'active' : ''} onClick={() => setTab(item.id)}>{item.label}</button>)}
        </div>
        <input aria-label="搜索记忆" value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索内容、ID、范围或来源" />
      </div>
      {loading ? <p className="memory-empty">正在加载记忆记录…</p> : visibleItems.length === 0 ? (
        <div className="memory-empty"><strong>暂无匹配记录</strong><span>{tab === 'archived' || tab === 'expired' || tab === 'rejected' ? '当前列表 API 只返回有效记忆和候选记录，不提供归档、过期或拒绝记录的全量查询；详情历史仍可追溯已知记忆。' : '调整筛选条件，或刷新后重试。'}</span></div>
      ) : <div className="memory-table-wrap"><table className="memory-table">
        <thead><tr><th>记忆内容</th><th>范围</th><th>状态</th><th>置信度</th><th>版本</th><th>有效期</th><th>操作</th></tr></thead>
        <tbody>{visibleItems.map(item => <tr key={item.memory_id}>
          <td><Link className="memory-content-link" to={'/workspace/memory/' + encodeURIComponent(item.memory_id)}>{item.content}</Link><small>{item.memory_id}</small><small>{item.source_ref || '未提供来源引用'}</small></td>
          <td>{item.scope}<small>{item.scope_id}</small></td>
          <td><span className={'memory-status status-' + item.status}>{item.status}</span></td>
          <td>{Math.round(item.confidence * 100)}%</td>
          <td>v{item.version}</td>
          <td>{item.expires_at ? new Date(item.expires_at).toLocaleString() : '长期'}</td>
          <td><div className="memory-actions">
            {item.status === 'candidate' || item.status === 'conflict' ? <button type="button" className="memory-button primary" disabled={busyId === item.memory_id} onClick={() => void confirm(item)}>{busyId === item.memory_id ? '处理中…' : '确认'}</button> : null}
            {item.status === 'active' ? <button type="button" className="memory-button secondary" disabled={busyId === item.memory_id} onClick={() => void archive(item)}>归档</button> : null}
          </div></td>
        </tr>)}</tbody>
      </table></div>}
    </section>
    <p className="memory-boundary">操作边界：服务端当前提供候选确认与有效记忆归档；未提供独立拒绝接口，因此本页不以永久删除代替拒绝。归档/拒绝状态的全量列表接口也尚未提供。</p>
  </section>
}

function MemoryDetail({ memoryId }: { memoryId: string }) {
  const [history, setHistory] = useState<MemoryRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const response = await apiClient.get<Envelope<MemoryRecord>>(
        basePath + '/' + encodeURIComponent(memoryId) + '/history',
      )
      setHistory(response.items ?? [])
    } catch (reason) {
      setError(errorMessage(reason))
    } finally {
      setLoading(false)
    }
  }, [memoryId])
  useEffect(() => { void load() }, [load])
  const current = history[history.length - 1]

  return <section className="page-content memory-page">
    <div className="memory-breadcrumb"><Link to="/workspace/memory">记忆治理</Link><span>/</span><span>记忆详情</span></div>
    <header className="memory-heading"><div><p className="eyebrow">MEMORY HISTORY</p><h1>记忆详情</h1><p>{memoryId}</p></div><button className="memory-button secondary" type="button" onClick={() => void load()} disabled={loading}>刷新历史</button></header>
    {error && <div className="memory-alert" role="alert">{error}</div>}
    {loading ? <p className="memory-empty">正在读取版本历史…</p> : !current ? <div className="memory-empty"><strong>没有可见的版本记录</strong><span>记录不存在，或当前身份无权查看。</span></div> : <>
      <section className="memory-panel memory-current">
        <div className="memory-panel-title"><h2>当前版本</h2><span className={'memory-status status-' + current.status}>{current.status}</span></div>
        <p className="memory-current-content">{current.content}</p>
        <dl><div><dt>范围</dt><dd>{current.scope} · {current.scope_id}</dd></div><div><dt>置信度</dt><dd>{Math.round(current.confidence * 100)}%</dd></div><div><dt>版本</dt><dd>v{current.version}</dd></div><div><dt>创建者</dt><dd>{current.created_by || '未记录'}</dd></div><div><dt>确认者</dt><dd>{current.confirmed_by || '未记录'}</dd></div><div><dt>有效期</dt><dd>{current.expires_at ? new Date(current.expires_at).toLocaleString() : '长期'}</dd></div></dl>
        <div className="memory-source"><strong>来源引用</strong>{current.source_ref ? /^https?:\/\//i.test(current.source_ref) ? <a href={current.source_ref} target="_blank" rel="noreferrer">{current.source_ref}</a> : <span>{current.source_ref}</span> : <span>未提供来源引用</span>}</div>
      </section>
      <section className="memory-panel"><div className="memory-panel-title"><h2>版本历史</h2><span>{history.length} 个版本</span></div>
        <ol className="memory-history">{[...history].reverse().map(item => <li key={item.memory_id + ':' + item.version}><div className="memory-history-marker" /><div className="memory-history-meta"><strong>版本 v{item.version}</strong><span>{item.recorded_at ? new Date(item.recorded_at).toLocaleString() : '时间未记录'}</span><span className={'memory-status status-' + item.status}>{item.status}</span></div><p>{item.content}</p><small>来源：{item.source_ref || '未记录'}</small></li>)}</ol>
      </section>
    </>}
  </section>
}
