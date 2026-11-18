import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiClient } from '@/lib/api'
import { EmptyState, ErrorState, LoadingState } from '@/components/States'

type RecordValue = Record<string, unknown>
type Envelope<T> = { items?: T[]; data?: T[] }
const unwrap = <T,>(value: unknown): T[] => {
  if (Array.isArray(value)) return value as T[]
  if (value && typeof value === 'object') {
    const record = value as Envelope<T>
    if (Array.isArray(record.items)) return record.items
    if (Array.isArray(record.data)) return record.data
  }
  return []
}
const text = (value: unknown, fallback = '—') =>
  typeof value === 'string' && value.trim() ? value : fallback
const date = (value: unknown) =>
  typeof value === 'string' ? new Date(value).toLocaleString() : '时间未提供'
const compact = (value: unknown) => {
  if (value === null || value === undefined) return '暂无摘要'
  const source = typeof value === 'string' ? value : JSON.stringify(value)
  return source.length > 220 ? source.slice(0, 220) + '…' : source
}
const errorText = (error: unknown) =>
  error instanceof Error ? error.message : '请求失败'

export function AgentCollaborationCenter() {
  const [scope, setScope] = useState('project')
  const [scopeId, setScopeId] = useState('')
  const [runId, setRunId] = useState('')
  const [selectedEntry, setSelectedEntry] = useState('')
  const [kind, setKind] = useState('')
  const agentsQuery = useQuery({
    queryKey: ['collaboration', 'agents'],
    queryFn: () => apiClient.get<unknown>('/api/platform/v1/registry/agent'),
    refetchInterval: 30000,
  })
  const agents = useMemo(() => unwrap<RecordValue>(agentsQuery.data), [agentsQuery.data])
  const runQuery = useQuery({
    queryKey: ['collaboration', 'run', runId],
    enabled: Boolean(runId.trim()),
    queryFn: () => apiClient.get<RecordValue>(`/api/platform/v1/runs/${encodeURIComponent(runId.trim())}`),
    refetchInterval: 10000,
  })
  const eventsQuery = useQuery({
    queryKey: ['collaboration', 'run-events', runId],
    enabled: Boolean(runId.trim()) && runQuery.isSuccess,
    queryFn: () => apiClient.get<unknown>(`/api/platform/v1/runs/${encodeURIComponent(runId.trim())}/events?limit=200`),
    refetchInterval: 10000,
  })
  const entriesQuery = useQuery({
    queryKey: ['collaboration', 'blackboard', scope, scopeId, kind],
    enabled: Boolean(scopeId.trim()),
    queryFn: () => {
      const params = new URLSearchParams({ scope, scope_id: scopeId.trim(), limit: '100' })
      if (kind) params.set('kinds', kind)
      return apiClient.get<unknown>(`/api/platform/v1/blackboard/entries?${params}`)
    },
    refetchInterval: 20000,
  })
  const entries = useMemo(() => unwrap<RecordValue>(entriesQuery.data), [entriesQuery.data])
  const historyQuery = useQuery({
    queryKey: ['collaboration', 'blackboard-history', selectedEntry],
    enabled: Boolean(selectedEntry),
    queryFn: () => apiClient.get<unknown>(`/api/platform/v1/blackboard/entries/${encodeURIComponent(selectedEntry)}/history`),
  })
  const history = unwrap<RecordValue>(historyQuery.data)
  const events = useMemo(() => {
    const items = unwrap<RecordValue>(eventsQuery.data)
    return [...items].sort((a, b) => {
      const sequenceA = typeof a.sequence === 'number' ? a.sequence : Number.MAX_SAFE_INTEGER
      const sequenceB = typeof b.sequence === 'number' ? b.sequence : Number.MAX_SAFE_INTEGER
      return sequenceA - sequenceB || String(a.occurred_at ?? '').localeCompare(String(b.occurred_at ?? ''))
    })
  }, [eventsQuery.data])

  return (
    <section className="page-content collaboration-center">
      <div className="page-heading">
        <p className="eyebrow">OPERATIONS / MULTI-AGENT</p>
        <h1>Agent 协作中心</h1>
        <p>查看 Agent 注册信息、真实 Run 事件与 Blackboard 不可变协作记录。</p>
      </div>

      <section className="collab-section" aria-labelledby="collab-team-title">
        <div className="collab-section-heading">
          <div><p className="eyebrow">TEAM DIRECTORY</p><h2 id="collab-team-title">Agent 团队</h2></div>
          <span className="muted">Registry · 30s 刷新</span>
        </div>
        {agentsQuery.isPending ? <LoadingState label="正在读取 Agent Registry" /> :
          agentsQuery.isError ? <ErrorState title="Agent 列表读取失败" description={errorText(agentsQuery.error)} onRetry={() => void agentsQuery.refetch()} /> :
          agents.length === 0 ? <EmptyState title="暂无已注册 Agent" description="Registry 未返回 Agent 定义；不会用示例卡片代替真实团队成员。" /> :
          <div className="collab-agent-grid">
            {agents.map((agent, index) => {
              const definition = (agent.definition && typeof agent.definition === 'object' ? agent.definition : {}) as RecordValue
              return <article className="collab-agent-card" key={text(agent.item_id, String(index))}>
                <div className="collab-agent-avatar" aria-hidden="true">{text(definition.name, text(agent.item_id, 'A')).slice(0, 1).toUpperCase()}</div>
                <div className="collab-agent-title"><strong>{text(definition.name, text(agent.item_id, '未命名 Agent'))}</strong><span>{text(definition.role, text(agent.kind, 'Agent'))}</span></div>
                <p>{text(definition.description, '该 Agent 尚未提供描述。')}</p>
                <dl><div><dt>定义版本</dt><dd>{text(agent.version, '—')}</dd></div><div><dt>发布状态</dt><dd>{text(agent.status, '未知')}</dd></div><div><dt>模型模板</dt><dd>{text(definition.model_template_id, '未声明')}</dd></div></dl>
                <small className="muted">ID: {text(agent.item_id)}</small>
              </article>
            })}
          </div>}
      </section>

      <section className="collab-section" aria-labelledby="collab-run-title">
        <div className="collab-section-heading"><div><p className="eyebrow">LANGGRAPH RUN OBSERVABILITY</p><h2 id="collab-run-title">Workflow Run 运行态</h2></div><span className="muted">真实 Run · 10s 刷新</span></div>
        <div className="collab-controls"><label>Run ID<input value={runId} onChange={event => setRunId(event.target.value)} placeholder="输入已存在的 Run ID" /></label></div>
        {!runId.trim() ? <EmptyState title="选择一个 Run" description="输入 Run ID 后读取服务端运行快照和事件。未提供 Run ID 时不展示虚构流程图。" /> :
          runQuery.isPending ? <LoadingState label="正在读取 Run 快照" /> :
          runQuery.isError ? <ErrorState title="Run 快照读取失败" description={errorText(runQuery.error)} onRetry={() => void runQuery.refetch()} /> :
          <div className="collab-run-layout">
            <article className="collab-run-summary">
              <span className="muted">RUN / {text(runQuery.data.run_id, runId)}</span>
              <strong>{text(runQuery.data.status, '状态未知')}</strong>
              <dl><div><dt>版本</dt><dd>{text(runQuery.data.version)}</dd></div><div><dt>更新时间</dt><dd>{date(runQuery.data.updated_at)}</dd></div><div><dt>Checkpoint</dt><dd>{text(runQuery.data.checkpoint_ref)}</dd></div></dl>
              {runQuery.data.last_error ? <p className="collab-error">{compact(runQuery.data.last_error)}</p> : null}
            </article>
            <div className="collab-event-panel">
              <h3>执行事件时间线</h3>
              {eventsQuery.isPending ? <LoadingState label="正在读取 Run 事件" /> :
                eventsQuery.isError ? <ErrorState title="Run 事件读取失败" description={errorText(eventsQuery.error)} onRetry={() => void eventsQuery.refetch()} /> :
                events.length === 0 ? <EmptyState title="暂无事件记录" description="事件接口没有返回节点级遥测；不会从 Run 总状态推断节点状态。" /> :
                <ol className="collab-event-list">{events.map((event, index) => <li key={text(event.event_id, `${String(event.sequence ?? 'event')}-${index}`)}>
                  <span className="collab-event-dot" />
                  <div><div className="collab-event-meta"><strong>{text(event.event_type, text(event.type, '运行事件'))}</strong><time>{date(event.occurred_at)}</time></div>
                    <p>{compact(event.payload ?? event.summary ?? event.data)}</p>
                    <small>sequence: {text(event.sequence, '未提供')} · trace: {text(event.trace_id, '未提供')}</small>
                  </div>
                </li>)}</ol>}
              <p className="muted">节点/边状态仅在服务端事件明确提供时呈现；当前事件列表不等同于完整 LangGraph 执行图。</p>
            </div>
          </div>}
      </section>

      <section className="collab-section" aria-labelledby="collab-blackboard-title">
        <div className="collab-section-heading"><div><p className="eyebrow">HYBRID BLACKBOARD</p><h2 id="collab-blackboard-title">Blackboard 协作时间线</h2></div><span className="muted">Entries · 20s 刷新</span></div>
        <div className="collab-controls collab-blackboard-controls">
          <label>范围类型<select value={scope} onChange={event => { setScope(event.target.value); setSelectedEntry('') }}><option value="project">Project</option><option value="task">Task</option><option value="run">Run</option><option value="tenant">Tenant</option></select></label>
          <label>范围 ID<input value={scopeId} onChange={event => { setScopeId(event.target.value); setSelectedEntry('') }} placeholder="输入有权访问的 scope_id" /></label>
          <label>Entry 类型<select value={kind} onChange={event => setKind(event.target.value)}><option value="">全部类型</option>{['evidence','claim','challenge','conflict','decision','fact','plan'].map(value => <option key={value} value={value}>{value}</option>)}</select></label>
        </div>
        {!scopeId.trim() ? <EmptyState title="选择 Blackboard 范围" description="输入 Project、Task、Run 或 Tenant 的范围 ID。实际可见数据由服务端授权决定。" /> :
          entriesQuery.isPending ? <LoadingState label="正在读取 Blackboard Entries" /> :
          entriesQuery.isError ? <ErrorState title="Blackboard 读取失败" description={errorText(entriesQuery.error)} onRetry={() => void entriesQuery.refetch()} /> :
          entries.length === 0 ? <EmptyState title="此范围暂无 Blackboard 记录" description="服务端未返回匹配 Entry。" /> :
          <div className="collab-blackboard-layout">
            <ol className="collab-entry-list">{entries.map((entry, index) => <li key={text(entry.entry_id, String(index))}>
              <button className={selectedEntry === entry.entry_id ? 'collab-entry selected' : 'collab-entry'} onClick={() => setSelectedEntry(text(entry.entry_id, ''))}>
                <span className="collab-entry-type">{text(entry.kind, 'entry')}</span><strong>{text((entry.content as RecordValue | undefined)?.title, text(entry.entry_id, '未命名条目'))}</strong>
                <span>{compact(entry.content)}</span><small>{text(entry.status)} · v{text(entry.version)} · {date(entry.created_at)}</small>
                <small>作者: {text(entry.author_id)} · 来源: {Array.isArray(entry.source_refs) ? entry.source_refs.length : 0}</small>
              </button>
            </li>)}</ol>
            <aside className="collab-history">
              <h3>版本历史</h3>
              {!selectedEntry ? <p className="muted">选择一条 Entry 查看版本链。</p> :
                historyQuery.isPending ? <LoadingState label="正在读取版本历史" /> :
                historyQuery.isError ? <ErrorState title="版本历史读取失败" description={errorText(historyQuery.error)} onRetry={() => void historyQuery.refetch()} /> :
                history.length === 0 ? <EmptyState title="暂无历史版本" description="仓库未返回版本记录。" /> :
                <ol>{history.map((version, index) => <li key={text(version.version, String(index))}><strong>版本 {text(version.version)}</strong><span>{text(version.status)} · {date(version.created_at)}</span><p>{compact(version.content)}</p><small>来源引用：{Array.isArray(version.source_refs) ? version.source_refs.map(String).join(', ') : '无'}</small></li>)}</ol>}
            </aside>
          </div>}
      </section>
    </section>
  )
}
