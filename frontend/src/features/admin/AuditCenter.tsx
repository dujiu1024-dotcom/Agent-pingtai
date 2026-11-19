import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { EmptyState, ErrorState, LoadingState } from '@/components/States'
import { apiClient } from '@/lib/api'
import './audit-center.css'

type AuditEvent = {
  event_id?: string
  audit_id?: string
  id?: string
  occurred_at?: string
  created_at?: string
  timestamp?: string
  actor_id?: string | null
  actor?: string | null
  action?: string
  resource?: string
  resource_type?: string
  result?: string
  run_id?: string
  [key: string]: unknown
}
type AuditResponse = { items?: AuditEvent[]; tenant_id?: string }
type AuditKind = 'tool-events' | 'identity-events'

const eventId = (event: AuditEvent) => String(event.event_id ?? event.audit_id ?? event.id ?? '')
const eventTime = (event: AuditEvent) => event.occurred_at ?? event.created_at ?? event.timestamp
const readable = (value: unknown) => {
  if (value == null || value === '') return '—'
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value)
}
const formatTime = (value: unknown) => {
  if (typeof value !== 'string') return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'medium' }).format(date)
}

function useAuditEvents(kind: AuditKind) {
  return useQuery({
    queryKey: ['audit-center', kind],
    queryFn: () => apiClient.get<AuditResponse>(`/platform/v1/audit/${kind}?limit=100`),
    refetchInterval: 30_000,
  })
}

export function AuditCenter() {
  const [kind, setKind] = useState<AuditKind>('tool-events')
  const [search, setSearch] = useState('')
  const query = useAuditEvents(kind)
  const events = useMemo(() => {
    const items = query.data?.items ?? []
    const needle = search.trim().toLowerCase()
    if (!needle) return items
    return items.filter((item) => [eventId(item), item.actor_id, item.actor, item.action, item.resource, item.resource_type, item.run_id]
      .some((value) => String(value ?? '').toLowerCase().includes(needle)))
  }, [query.data?.items, search])

  return (
    <section className="page-content audit-page">
      <header className="page-heading audit-heading">
        <div><p className="eyebrow">PLATFORM / GOVERNANCE</p><h1>审计中心</h1><p>只读审计事件查询。数据由服务端按当前身份与租户权限过滤并脱敏。</p></div>
        <span className="audit-readonly">APPEND-ONLY · READ ONLY</span>
      </header>
      <div className="audit-toolbar">
        <div className="audit-tabs" role="tablist" aria-label="审计事件类型">
          <button type="button" role="tab" aria-selected={kind === 'tool-events'} className={kind === 'tool-events' ? 'selected' : ''} onClick={() => setKind('tool-events')}>工具事件</button>
          <button type="button" role="tab" aria-selected={kind === 'identity-events'} className={kind === 'identity-events' ? 'selected' : ''} onClick={() => setKind('identity-events')}>身份事件</button>
        </div>
        <label className="audit-search">筛选当前结果<input value={search} onChange={(event) => setSearch(event.target.value)} placeholder={kind === 'tool-events' ? '事件、操作者、资源或 Run ID' : '事件、操作者或 Action'} /></label>
        <span className="audit-count">{events.length} 条记录</span>
      </div>
      {query.isPending ? <LoadingState label="正在读取审计事件" /> : query.isError ? (
        <ErrorState title="审计事件暂不可用" description={query.error instanceof Error ? query.error.message : '服务端审计接口请求失败'} onRetry={() => void query.refetch()} />
      ) : events.length === 0 ? (
        <EmptyState title="暂无匹配的审计事件" description="当前接口返回空列表，或筛选条件没有匹配项。不会用演示数据填充审计记录。" />
      ) : (
        <div className="audit-table-wrap"><table className="audit-table"><thead><tr><th>发生时间</th><th>操作者</th><th>操作</th><th>资源</th><th>结果</th><th>事件 ID</th></tr></thead><tbody>
          {events.map((event, index) => {
            const id = eventId(event)
            return <tr key={id || index}><td>{formatTime(eventTime(event))}</td><td>{readable(event.actor_id ?? event.actor)}</td><td>{readable(event.action)}</td><td>{readable(event.resource ?? event.resource_type)}</td><td><span className="audit-result">{readable(event.result)}</span></td><td>{id ? <Link to={`/admin/audit/${encodeURIComponent(id)}`}>{id}</Link> : <span className="muted">无稳定事件 ID</span>}</td></tr>
          })}
        </tbody></table></div>
      )}
      <p className="audit-footnote">自动刷新间隔 30 秒 · 每类接口最多读取 100 条 · 当前 API 不提供写入、删除或批量导出操作。</p>
    </section>
  )
}

export function AuditEventDetail() {
  const { eventId: requestedId = '' } = useParams()
  const decodedId = decodeURIComponent(requestedId)
  const [kind, setKind] = useState<AuditKind>('tool-events')
  const query = useAuditEvents(kind)
  const event = query.data?.items?.find((item) => eventId(item) === decodedId)

  return (
    <section className="page-content audit-page">
      <header className="page-heading"><p className="eyebrow">PLATFORM / AUDIT EVENT</p><h1>审计事件详情</h1><p>事件详情从只读列表响应中定位；后端当前未提供单条事件读取端点。</p></header>
      <div className="audit-detail-toolbar"><Link className="text-link" to="/admin/audit">← 返回审计中心</Link><div className="audit-tabs"><button type="button" className={kind === 'tool-events' ? 'selected' : ''} onClick={() => setKind('tool-events')}>工具事件</button><button type="button" className={kind === 'identity-events' ? 'selected' : ''} onClick={() => setKind('identity-events')}>身份事件</button></div></div>
      {query.isPending ? <LoadingState label="正在定位审计事件" /> : query.isError ? <ErrorState title="审计事件暂不可用" description={query.error instanceof Error ? query.error.message : '服务端审计接口请求失败'} onRetry={() => void query.refetch()} /> : !event ? <EmptyState title="当前列表中未找到该事件" description="详情查询受限于后端最多返回 100 条的列表接口；事件可能不在当前窗口，或属于另一类事件。" /> : (
        <section className="audit-detail-card"><div className="audit-detail-title"><div><span className="audit-readonly">READ ONLY</span><h2>{readable(event.action)}</h2><p>{decodedId}</p></div><time>{formatTime(eventTime(event))}</time></div><dl>{Object.entries(event).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{readable(value)}</dd></div>)}</dl></section>
      )}
    </section>
  )
}
