import { useMemo } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { apiClient } from '@/lib/api'
import { EmptyState, ErrorState, LoadingState } from '@/components/States'

type AuditEvent = Record<string, unknown> & {
  id?: string
  audit_id?: string
  event_id?: string
  created_at?: string
  timestamp?: string
  actor_id?: string
  action?: string
  resource_type?: string
  resource_id?: string
  result?: string
  run_id?: string
}

type AuditResponse = { items?: AuditEvent[] }

function items(value: unknown): AuditEvent[] {
  if (Array.isArray(value)) return value as AuditEvent[]
  if (value && typeof value === 'object' && Array.isArray((value as AuditResponse).items))
    return (value as AuditResponse).items ?? []
  return []
}

const display = (value: unknown) => {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value)
}
const eventId = (event: AuditEvent) => display(event.audit_id ?? event.event_id ?? event.id)
const eventTime = (event: AuditEvent) => display(event.created_at ?? event.timestamp)
const eventAction = (event: AuditEvent) => display(event.action ?? event.event_type ?? event.type)
const eventResource = (event: AuditEvent) => [event.resource_type, event.resource_id].filter(Boolean).map(display).join(' / ') || '—'
const eventActor = (event: AuditEvent) => display(event.actor_id ?? event.actor ?? event.subject_id)

export function AuditCenterPage() {
  const { auditId } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const toolQuery = useQuery({
    queryKey: ['audit', 'tool-events'],
    queryFn: () => apiClient.get<unknown>('/platform/v1/audit/tool-events?limit=100').then(items),
    refetchInterval: 15000,
  })
  const identityQuery = useQuery({
    queryKey: ['audit', 'identity-events'],
    queryFn: () => apiClient.get<unknown>('/platform/v1/audit/identity-events?limit=100').then(items),
    refetchInterval: 15000,
  })
  const events = useMemo(() => [
    ...(toolQuery.data ?? []).map((event) => ({ ...event, _source: 'tool' })),
    ...(identityQuery.data ?? []).map((event) => ({ ...event, _source: 'identity' })),
  ].sort((a, b) => Date.parse(eventTime(b)) - Date.parse(eventTime(a))), [toolQuery.data, identityQuery.data])
  const selected = auditId ? events.find((event) => eventId(event) === auditId) : undefined
  const query = (searchParams.get('q') ?? '').trim().toLowerCase()
  const filtered = events.filter((event) => !query || [
    eventId(event), eventTime(event), eventActor(event), eventAction(event),
    eventResource(event), display(event.result), display(event.run_id),
  ].join(' ').toLowerCase().includes(query))
  const failed = toolQuery.isError && identityQuery.isError
  const loading = toolQuery.isPending || identityQuery.isPending
  const error = [toolQuery.error, identityQuery.error].filter(Boolean).map((value) => value instanceof Error ? value.message : '请求失败').join('；')

  if (auditId) {
    if (loading) return <section className="page-content"><LoadingState label="正在读取审计事件" /></section>
    if (failed) return <section className="page-content"><ErrorState title="审计详情加载失败" description={error} onRetry={() => { void toolQuery.refetch(); void identityQuery.refetch() }} /></section>
    if (!selected) return <section className="page-content audit-center"><Link to="/admin/audit">← 返回审计中心</Link><div className="page-heading"><h1>未找到审计事件</h1><p>该 ID 不在当前 API 返回的最近 100 条工具/身份事件中。</p></div></section>
    return (
      <section className="page-content audit-center">
        <Link to="/admin/audit">← 返回审计中心</Link>
        <div className="page-heading"><p className="eyebrow">PLATFORM / AUDIT / {eventId(selected)}</p><h1>审计事件详情</h1><p>只读记录 · {selected._source === 'tool' ? '工具调用事件' : '身份事件'}</p></div>
        <section className="task-detail-panel"><h2>事件字段</h2><dl>
          {Object.entries(selected).filter(([key]) => !key.startsWith('_')).map(([key, value]) => <div key={key}><dt>{key}</dt><dd className="audit-value">{display(value)}</dd></div>)}
        </dl></section>
        <p className="task-data-note">审计记录为只读数据。页面不提供编辑、删除或补写操作。</p>
      </section>
    )
  }

  return (
    <section className="page-content audit-center">
      <div className="page-heading task-heading"><div><p className="eyebrow">PLATFORM / GOVERNANCE</p><h1>审计中心</h1><p>按时间查看工具调用与身份事件。数据由服务端审计 API 提供，页面仅查询。</p></div><small>自动刷新 · 15s</small></div>
      <div className="task-toolbar">
        <input aria-label="搜索审计记录" placeholder="搜索 actor / action / resource / ID" value={searchParams.get('q') ?? ''} onChange={(event) => { const next = new URLSearchParams(searchParams); if (event.target.value) next.set('q', event.target.value); else next.delete('q'); setSearchParams(next) }} />
        <button type="button" className="button-link" onClick={() => { void toolQuery.refetch(); void identityQuery.refetch() }}>刷新</button>
      </div>
      {loading ? <LoadingState label="正在加载审计记录" /> : failed ? <ErrorState title="审计记录加载失败" description={error} onRetry={() => { void toolQuery.refetch(); void identityQuery.refetch() }} /> : !filtered.length ? <EmptyState title="暂无审计记录" description="当前筛选条件下，服务端没有返回审计事件。" /> : (
        <div className="task-table-wrap"><table className="task-table"><thead><tr><th>时间</th><th>Actor</th><th>Action</th><th>Resource</th><th>Result</th><th>来源</th><th>ID</th></tr></thead><tbody>
          {filtered.map((event, index) => <tr key={eventId(event) === '—' ? `${event._source}-${index}` : `${event._source}-${eventId(event)}`}><td>{eventTime(event)}</td><td>{eventActor(event)}</td><td>{eventAction(event)}</td><td>{eventResource(event)}</td><td>{display(event.result ?? event.status)}</td><td>{event._source === 'tool' ? '工具' : '身份'}</td><td><Link to={`/admin/audit/${encodeURIComponent(eventId(event))}`}>{eventId(event)}</Link></td></tr>)}
        </tbody></table></div>
      )}
      <p className="task-data-note">当前列表分别读取 tool-events 与 identity-events，各取最近 100 条；筛选在客户端执行。接口未提供统一分页/详情读取契约，因此详情从当前结果集定位，不伪造服务端详情 API。</p>
    </section>
  )
}
