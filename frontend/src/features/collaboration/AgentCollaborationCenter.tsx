import { useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, apiClient } from '@/lib/api'
import { EmptyState, ErrorState, LoadingState } from '@/components/States'
import { eventLabel, localizedJson, valueLabel } from '@/lib/i18n/labels'
import './AgentCollaborationCenter.css'

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
const date = (value: unknown) => {
  if (typeof value !== 'string') return '时间未提供'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(
    d.getHours(),
  )}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}
const compact = (value: unknown) => {
  if (value === null || value === undefined) return '暂无摘要'
  const source = typeof value === 'string' ? value : localizedJson(value)
  return source.length > 220 ? source.slice(0, 220) + '…' : source
}
const errorText = (error: unknown) =>
  error instanceof Error ? error.message : '请求失败'
const blackboardError = (error: unknown) => {
  if (error instanceof ApiError) {
    if (error.status === 400) return '请求参数不完整：请检查内容、原因、决策依据或证据引用。'
    if (error.status === 403) return '当前身份无权访问或修改该黑板范围。'
    if (error.status === 404) return '黑板条目不存在，或已被当前身份不可见。'
    if (error.status === 409) return '版本冲突：条目已被他人更新，请刷新后基于最新版本重试。'
    if (error.status === 422) return '证据引用缺失或不可访问，请检查来源引用后重试。'
    if (error.status === 503) return '服务端未启用证据校验或事件流能力，请稍后再试。'
    return error.message
  }
  return error instanceof Error ? error.message : '请求失败，请稍后重试。'
}
const splitRefs = (value: string) =>
  value
    .split(/[\n,，]/)
    .map(item => item.trim())
    .filter(Boolean)
const entryVersion = (entry: RecordValue | undefined) => {
  const value = entry?.version
  const version = typeof value === 'number' ? value : Number(value)
  return Number.isInteger(version) && version >= 1 ? version : 0
}

export function AgentCollaborationCenter() {
  const queryClient = useQueryClient()
  const [scope, setScope] = useState('')
  const [scopeId, setScopeId] = useState('')
  const [runId, setRunId] = useState('')
  const [selectedEntry, setSelectedEntry] = useState('')
  const [kind, setKind] = useState('')
  const [tab, setTab] = useState<'team' | 'runtime' | 'blackboard'>('team')
  const [liveRefresh, setLiveRefresh] = useState(false)
  const [liveStatus, setLiveStatus] = useState<'idle' | 'connecting' | 'connected' | 'error'>('idle')
  const [lastLiveEvent, setLastLiveEvent] = useState('')
  const [editContent, setEditContent] = useState('{}')
  const [editError, setEditError] = useState('')
  const [challengeReason, setChallengeReason] = useState('')
  const [challengeRefs, setChallengeRefs] = useState('')
  const [decisionOutcome, setDecisionOutcome] = useState('')
  const [decisionRationale, setDecisionRationale] = useState('')
  const [decisionRefs, setDecisionRefs] = useState('')
  // TODO(#746): Replace this read adapter with the finalized Agent Registry contract
  // when backend pagination and live-health fields are available.
  const agentsQuery = useQuery({
    queryKey: ['collaboration', 'agents'],
    queryFn: () => apiClient.get<unknown>('/platform/v1/registry/agent'),
    refetchInterval: 30000,
  })
  const agents = useMemo(() => unwrap<RecordValue>(agentsQuery.data), [agentsQuery.data])
  // 最近 Agent 运行列表（run_store），供工作流运行态与协作黑板 Tab 快速选择
  const runsQuery = useQuery({
    queryKey: ['collaboration', 'recent-runs'],
    queryFn: () => apiClient.get<unknown>('/platform/v1/runs?limit=10'),
    refetchInterval: 30000,
  })
  const recentRuns = useMemo(() => unwrap<RecordValue>(runsQuery.data), [runsQuery.data])

  // 自动选中第一个有效 run 作为默认值
  useEffect(() => {
    if (runId.trim() || recentRuns.length === 0) return
    const firstRun = recentRuns.find(r => text(r.run_id, '').trim())
    if (firstRun) setRunId(text(firstRun.run_id, ''))
  }, [recentRuns, runId])

  // 协作黑板默认空 scopeId = tenant 级全部，无需自动填充

  // TODO(#746): Bind the runtime canvas to LangGraph node/edge telemetry once
  // the backend publishes its versioned graph snapshot and event schema.
  const runQuery = useQuery({
    queryKey: ['collaboration', 'run', runId],
    enabled: Boolean(runId.trim()),
    queryFn: () => apiClient.get<RecordValue>(`/platform/v1/runs/${encodeURIComponent(runId.trim())}`),
    refetchInterval: 10000,
  })
  const eventsQuery = useQuery({
    queryKey: ['collaboration', 'run-events', runId],
    enabled: Boolean(runId.trim()) && runQuery.isSuccess,
    queryFn: () => apiClient.get<unknown>(`/platform/v1/runs/${encodeURIComponent(runId.trim())}/events?limit=200`),
    refetchInterval: 10000,
  })
  // TODO(#746): Extend this adapter with authorized evidence/source locators
  // after the Blackboard locator contract is finalized.
  const entriesQuery = useQuery({
    queryKey: ['collaboration', 'blackboard', scope, scopeId, kind],
    enabled: true,
    queryFn: () => {
      const hasScopeFilter = Boolean(scopeId.trim())
      const params = new URLSearchParams({ limit: '200' })
      if (kind) params.set('kinds', kind)
      if (hasScopeFilter) {
        params.set('scope', scope)
        params.set('scope_id', scopeId.trim())
        return apiClient.get<unknown>(`/platform/v1/blackboard/entries?${params}`)
      }
      // 默认列出 tenant 级全部 entries，可选 scope 过滤（不需要 scope_id）
      if (scope && scope !== 'tenant') params.set('scope', scope)
      return apiClient.get<unknown>(`/platform/v1/blackboard/entries/all?${params}`)
    },
    refetchInterval: liveRefresh && Boolean(scopeId.trim()) ? false : 20000,
  })
  const entries = useMemo(() => unwrap<RecordValue>(entriesQuery.data), [entriesQuery.data])
  const selected = useMemo(
    () => entries.find(entry => text(entry.entry_id, '') === selectedEntry),
    [entries, selectedEntry],
  )
  const historyQuery = useQuery({
    queryKey: ['collaboration', 'blackboard-history', selectedEntry],
    enabled: Boolean(selectedEntry),
    queryFn: () => apiClient.get<unknown>(`/platform/v1/blackboard/entries/${encodeURIComponent(selectedEntry)}/history`),
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

  const invalidateBlackboard = () => {
    void queryClient.invalidateQueries({ queryKey: ['collaboration', 'blackboard'] })
    void queryClient.invalidateQueries({ queryKey: ['collaboration', 'blackboard-history'] })
  }

  const reviseMutation = useMutation({
    mutationFn: (input: { entryId: string; content: RecordValue; expectedVersion: number }) =>
      apiClient.patch(
        `/platform/v1/blackboard/entries/${encodeURIComponent(input.entryId)}`,
        { content: input.content, expected_version: input.expectedVersion },
      ),
    onSuccess: invalidateBlackboard,
  })
  const challengeMutation = useMutation({
    mutationFn: (input: { entryId: string; reason: string; refs: string[] }) =>
      apiClient.post(
        `/platform/v1/blackboard/entries/${encodeURIComponent(input.entryId)}/challenges`,
        { reason: input.reason, evidence_refs: input.refs },
      ),
    onSuccess: invalidateBlackboard,
  })
  const decisionMutation = useMutation({
    mutationFn: (input: { entryId: string; outcome: string; rationale: string; refs: string[] }) =>
      apiClient.post(
        `/platform/v1/blackboard/entries/${encodeURIComponent(input.entryId)}/decisions`,
        { outcome: input.outcome, rationale: input.rationale, evidence_refs: input.refs },
      ),
    onSuccess: invalidateBlackboard,
  })

  useEffect(() => {
    if (!liveRefresh || !scopeId.trim()) {
      setLiveStatus('idle')
      return
    }
    if (typeof EventSource === 'undefined') {
      setLiveStatus('error')
      return
    }
    const params = new URLSearchParams({ scope, scope_id: scopeId.trim() })
    const source = new EventSource(`/api/platform/v1/blackboard/events/stream?${params.toString()}`)
    setLiveStatus('connecting')
    source.onopen = () => setLiveStatus('connected')
    source.onerror = () => setLiveStatus('error')
    const handleUpdate = (event: MessageEvent<string>) => {
      setLiveStatus('connected')
      try {
        const payload = JSON.parse(event.data) as RecordValue
        setLastLiveEvent(
          `${date(payload.occurred_at)} · ${eventLabel(text(payload.event_type, '黑板更新'))} · 条目 ${text(payload.entry_id)}`,
        )
      } catch {
        setLastLiveEvent('收到实时更新事件。')
      }
      void queryClient.invalidateQueries({ queryKey: ['collaboration', 'blackboard'] })
      void queryClient.invalidateQueries({ queryKey: ['collaboration', 'blackboard-history'] })
    }
    source.addEventListener('blackboard_update', handleUpdate as EventListener)
    return () => {
      source.removeEventListener('blackboard_update', handleUpdate as EventListener)
      source.close()
    }
  }, [liveRefresh, scope, scopeId, queryClient])

  const submitRevision = (event: FormEvent) => {
    event.preventDefault()
    if (!selected || !selectedEntry) return
    const version = entryVersion(selected)
    if (!version) {
      setEditError('条目版本缺失，无法提交修订。')
      return
    }
    let content: RecordValue
    try {
      const parsed: unknown = JSON.parse(editContent)
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('内容必须是 JSON 对象')
      }
      content = parsed as RecordValue
    } catch (reason) {
      setEditError(reason instanceof Error ? reason.message : '内容必须是合法的 JSON 对象。')
      return
    }
    setEditError('')
    reviseMutation.mutate({ entryId: selectedEntry, content, expectedVersion: version })
  }
  const submitChallenge = (event: FormEvent) => {
    event.preventDefault()
    if (!selectedEntry || !challengeReason.trim()) return
    challengeMutation.mutate({
      entryId: selectedEntry,
      reason: challengeReason.trim(),
      refs: splitRefs(challengeRefs),
    })
  }
  const submitDecision = (event: FormEvent) => {
    event.preventDefault()
    if (!selectedEntry || !decisionOutcome.trim() || !decisionRationale.trim()) return
    decisionMutation.mutate({
      entryId: selectedEntry,
      outcome: decisionOutcome.trim(),
      rationale: decisionRationale.trim(),
      refs: splitRefs(decisionRefs),
    })
  }

  return (
    <section className="page-content collaboration-center">
      <div className="page-heading">
        <p className="eyebrow">运营 · 多 Agent</p>
        <h1>Agent 协作中心</h1>
        <p>查看 Agent 注册信息、真实运行事件与协作黑板不可变记录。</p>
      </div>

      <nav className="panel-tabs" role="tablist" aria-label="Agent 协作分区">
        {([
          ['team', 'Agent 团队', agents.length],
          ['runtime', '工作流运行态', events.length],
          ['blackboard', '协作黑板时间线', entries.length],
        ] as const).map(([id, label, count]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            className={tab === id ? 'panel-tab active' : 'panel-tab'}
            onClick={() => setTab(id)}
          >
            {label}
            <span className="panel-tab-count">{count}</span>
          </button>
        ))}
      </nav>

      {tab === 'team' && (
      <section className="collab-section" aria-labelledby="collab-team-title">
        <div className="collab-section-heading">
          <div><p className="eyebrow">团队目录</p><h2 id="collab-team-title">Agent 团队</h2></div>
          <span className="muted">Registry · 30 秒刷新</span>
        </div>
        {agentsQuery.isPending ? <LoadingState label="正在读取 Agent Registry" /> :
          agentsQuery.isError ? <ErrorState title="Agent 列表读取失败" description={errorText(agentsQuery.error)} onRetry={() => void agentsQuery.refetch()} /> :
          agents.length === 0 ? <EmptyState title="暂无已注册 Agent" description="Registry 未返回 Agent 定义；不会用示例卡片代替真实团队成员。" /> :
          <div className="collab-agent-grid">
            {agents.map((agent, index) => {
              const definition = (agent.definition && typeof agent.definition === 'object' ? agent.definition : {}) as RecordValue
              return <Link to={`/admin/agents/${agent.item_id}`} key={text(agent.item_id, String(index))} className="collab-agent-card" aria-label={`查看 Agent ${text(definition.name, text(agent.item_id, ''))} 详情`}>
                <div className="collab-agent-avatar" aria-hidden="true">{text(definition.name, text(agent.item_id, 'A')).slice(0, 1).toUpperCase()}</div>
                <div className="collab-agent-title"><strong>{text(definition.name, text(agent.item_id, '未命名 Agent'))}</strong><span>{text(valueLabel(definition.role), valueLabel(agent.kind))}</span></div>
                <p>{text(definition.description, '该 Agent 尚未提供描述。')}</p>
                <dl><div><dt>定义版本</dt><dd>{text(agent.version, '—')}</dd></div><div><dt>发布状态</dt><dd>{valueLabel(agent.status)}</dd></div><div><dt>模型模板</dt><dd>{text(definition.model_template_id, '未声明')}</dd></div></dl>
                <small className="muted">ID: {text(agent.item_id)}</small>
              </Link>
            })}
          </div>}
      </section>
      )}

      {tab === 'runtime' && (
      <section className="collab-section" aria-labelledby="collab-run-title">
        <div className="collab-section-heading"><div><p className="eyebrow">运行可观测</p><h2 id="collab-run-title">工作流运行态</h2></div><span className="muted">实时运行 · 10 秒刷新</span></div>
        <div className="collab-controls"><label>快速选择最近运行
          <select value={recentRuns.some(r => text(r.run_id, '') === runId) ? runId : ''} onChange={event => setRunId(event.target.value)}>
            <option value="">— 手动输入或选择下方运行 —</option>
            {recentRuns.map((r, idx) => {
              const id = text(r.run_id, '')
              if (!id) return null
              return <option key={id} value={id}>{valueLabel(r.status)} · {id} · {date(r.updated_at ?? r.created_at)} ({idx + 1}/{recentRuns.length})</option>
            })}
          </select>
        </label><label>运行 ID<input value={runId} onChange={event => setRunId(event.target.value)} placeholder="也可以手动输入运行 ID" /></label>{runsQuery.isPending && <span className="muted">正在拉取最近运行…</span>}{runsQuery.isError && <span className="muted">最近运行列表获取失败：{errorText(runsQuery.error)}</span>}</div>
        {!runId.trim() ? <EmptyState title="选择一个运行" description={recentRuns.length > 0 ? '从下拉框选择一个最新运行，或手动输入运行 ID。' : '暂无最近运行，请手动输入运行 ID 或先触发一次运行。'} /> :
          runQuery.isPending ? <LoadingState label="正在读取运行快照" /> :
          runQuery.isError ? <ErrorState title="运行快照读取失败" description={errorText(runQuery.error)} onRetry={() => void runQuery.refetch()} /> :
          <div className="collab-run-layout">
            <article className="collab-run-summary">
              <span className="muted">运行 / {text(runQuery.data.run_id, runId)}</span>
              <strong>{valueLabel(runQuery.data.status)}</strong>
              <dl><div><dt>工作流</dt><dd>{text(runQuery.data.workflow_id)}</dd></div><div><dt>工作流版本</dt><dd>{text(runQuery.data.workflow_version)}</dd></div><div><dt>运行版本</dt><dd>{text(runQuery.data.version)}</dd></div><div><dt>更新时间</dt><dd>{date(runQuery.data.updated_at)}</dd></div><div><dt>检查点</dt><dd>{text(runQuery.data.checkpoint_ref)}</dd></div></dl>
              {runQuery.data.last_error ? <p className="collab-error">{compact(runQuery.data.last_error)}</p> : null}
            </article>
            <div className="collab-run-observability">
              <section className="collab-canvas-placeholder" aria-label="工作流运行画布占位">
                <div>
                  <span className="eyebrow">运行画布</span>
                  <h3>运行态画布</h3>
                  <p>当前运行已加载。节点、边、分支与状态映射等待服务端图快照/节点事件契约接入。</p>
                  <span className="muted">工作流 ID：{text(runQuery.data.workflow_id)} · 版本：{text(runQuery.data.workflow_version)} · 运行 ID：{text(runQuery.data.run_id, runId)}</span>
                </div>
                <div className="collab-canvas-empty" role="status">
                  <strong>暂无节点级遥测</strong>
                  <span>不会根据父运行状态推断节点成功、失败或分支路径。</span>
                </div>
              </section>
              <div className="collab-event-panel">
              <h3>执行事件时间线（列表替代视图）</h3>
              {eventsQuery.isPending ? <LoadingState label="正在读取运行事件" /> :
                eventsQuery.isError ? <ErrorState title="运行事件读取失败" description={errorText(eventsQuery.error)} onRetry={() => void eventsQuery.refetch()} /> :
                events.length === 0 ? <EmptyState title="暂无事件记录" description="事件接口没有返回节点级遥测；不会从运行总状态推断节点状态。" /> :
                <ol className="collab-event-list">{events.map((event, index) => <li key={text(event.event_id, `${String(event.sequence ?? 'event')}-${index}`)}>
                  <span className="collab-event-dot" />
                  <div><div className="collab-event-meta"><strong>{eventLabel(text(event.event_type, text(event.type, '运行事件')))}</strong><time>{date(event.occurred_at)}</time></div>
                    <p>{compact(event.payload ?? event.summary ?? event.data)}</p>
                    <small>sequence: {text(event.sequence, '未提供')} · trace: {text(event.trace_id, '未提供')}</small>
                  </div>
                </li>)}</ol>}
              <p className="muted">节点/边状态仅在服务端事件明确提供时呈现；当前事件列表不等同于完整 LangGraph 执行图。</p>
              </div>
            </div>
          </div>}
      </section>
      )}

      {tab === 'blackboard' && (
      <section className="collab-section" aria-labelledby="collab-blackboard-title">
        <div className="collab-section-heading"><div><p className="eyebrow">协作黑板</p><h2 id="collab-blackboard-title">协作黑板时间线</h2></div><span className="muted">条目 · {liveRefresh && scopeId.trim() ? '实时事件流' : '20 秒刷新'}</span></div>
        <div className="collab-controls collab-blackboard-controls">
          <label>范围类型<select value={scope} onChange={event => { setScope(event.target.value); setSelectedEntry('') }}><option value="">全部</option><option value="project">项目</option><option value="task">任务</option><option value="run">运行</option><option value="tenant">租户</option></select></label>
          {recentRuns.length > 0 && (
            <label>使用最近运行
              <select value={scope === 'run' && recentRuns.some(r => text(r.run_id, '') === scopeId) ? scopeId : ''} onChange={event => { if (event.target.value) { setScope('run'); setScopeId(event.target.value); setSelectedEntry('') } }}>
                <option value="">— 选择一个运行 —</option>
                {recentRuns.map(r => {
                  const id = text(r.run_id, '')
                  if (!id) return null
                  return <option key={id} value={id}>{valueLabel(r.status)} · {id}</option>
                })}
              </select>
            </label>
          )}
          <label>范围 ID<input value={scopeId} onChange={event => { setScopeId(event.target.value); setSelectedEntry('') }} placeholder="留空 = 展示全部条目" /></label>
          <label>条目类型<select value={kind} onChange={event => setKind(event.target.value)}><option value="">全部类型</option>{['fact','claim','evidence','challenge','conflict','decision','artifact','note'].map(value => <option key={value} value={value}>{valueLabel(value)}</option>)}</select></label>
          <label className="collab-bb-toggle"><input type="checkbox" checked={liveRefresh} onChange={event => setLiveRefresh(event.target.checked)} disabled={!scopeId.trim()} />实时刷新（SSE）</label>
        </div>
        <div className={liveStatus === 'error' ? 'collab-bb-live error' : 'collab-bb-live'}>
          <strong>{liveRefresh ? { idle: '待启用', connecting: '连接中…', connected: '已连接', error: '连接中断，将自动重连' }[liveStatus] : '未开启实时刷新'}</strong>
          {lastLiveEvent && <span>最近事件：{lastLiveEvent}</span>}
          <span>实时开启时暂停 20 秒轮询；关闭后恢复轮询。</span>
        </div>
        {entriesQuery.isPending ? <LoadingState label="正在读取黑板条目" /> :
          entriesQuery.isError ? <ErrorState title="协作黑板读取失败" description={errorText(entriesQuery.error)} onRetry={() => void entriesQuery.refetch()} /> :
          entries.length === 0 ? <EmptyState title="暂无黑板记录" description={scopeId.trim() ? `范围 ${scope}:${scopeId} 下无条目。` : '当前租户下尚未写入任何黑板条目。'} /> :
          <div className="collab-blackboard-layout">
            <ol className="collab-entry-list">{entries.map((entry, index) => <li key={text(entry.entry_id, String(index))}>
              <button
                className={selectedEntry === entry.entry_id ? 'collab-entry selected' : 'collab-entry'}
                onClick={() => {
                  setSelectedEntry(text(entry.entry_id, ''))
                  setEditContent(JSON.stringify(entry.content ?? {}, null, 2))
                  setEditError('')
                  reviseMutation.reset()
                  challengeMutation.reset()
                  decisionMutation.reset()
                }}
              >
                <span className="collab-entry-type">{valueLabel(entry.kind)}</span><strong>{text((entry.content as RecordValue | undefined)?.title, text(entry.entry_id, '未命名条目'))}</strong>
                <span>{compact(entry.content)}</span><small>{valueLabel(entry.status)} · v{text(entry.version)} · {date(entry.created_at)}</small>
                <small>作者：{text(entry.author_id)} · 来源：{Array.isArray(entry.source_refs) ? entry.source_refs.length : 0}</small>
              </button>
            </li>)}</ol>
            <aside className="collab-history">
              <h3>版本历史</h3>
              {!selectedEntry ? <p className="muted">选择一条条目查看版本链。</p> :
                historyQuery.isPending ? <LoadingState label="正在读取版本历史" /> :
                historyQuery.isError ? <ErrorState title="版本历史读取失败" description={blackboardError(historyQuery.error)} onRetry={() => void historyQuery.refetch()} /> :
                history.length === 0 ? <EmptyState title="暂无历史版本" description="仓库未返回版本记录。" /> :
                <ol>{history.map((version, index) => <li key={text(version.version, String(index))}><strong>版本 {text(version.version)}</strong><span>{valueLabel(version.status)} · {date(version.created_at)}</span><p>{compact(version.content)}</p><small>来源引用：{Array.isArray(version.source_refs) ? version.source_refs.map(String).join(', ') : '无'}</small></li>)}</ol>}

              {selectedEntry && selected && <div className="collab-bb-actions">
                <div className="collab-bb-meta">
                  <span>条目 {text(selected.entry_id)}</span>
                  <span>当前版本 v{text(selected.version)}</span>
                  <span>状态 {valueLabel(selected.status)}</span>
                </div>

                <form className="collab-bb-form" onSubmit={submitRevision}>
                  <h4>编辑内容（生成新版本）</h4>
                  <label>内容 JSON<textarea className="collab-bb-json" rows={6} value={editContent} onChange={event => setEditContent(event.target.value)} spellCheck={false} /></label>
                  <p className="collab-bb-hint">提交时会携带当前版本号；若版本已变化，服务端返回 409 冲突。</p>
                  {editError && <p className="collab-bb-error">{editError}</p>}
                  {reviseMutation.isError && <p className="collab-bb-error">{blackboardError(reviseMutation.error)}</p>}
                  {reviseMutation.isSuccess && <p className="collab-bb-success">修订已保存，版本历史已刷新。</p>}
                  <button className="button-primary" disabled={reviseMutation.isPending}>{reviseMutation.isPending ? '提交中…' : '保存修订'}</button>
                </form>

                <form className="collab-bb-form" onSubmit={submitChallenge}>
                  <h4>发起挑战</h4>
                  <label>挑战原因<textarea rows={3} value={challengeReason} onChange={event => setChallengeReason(event.target.value)} required /></label>
                  <label>证据引用（可选，逗号或换行分隔）<textarea rows={2} value={challengeRefs} onChange={event => setChallengeRefs(event.target.value)} placeholder="例如 evidence://run/123#chunk-1" /></label>
                  {challengeMutation.isError && <p className="collab-bb-error">{blackboardError(challengeMutation.error)}</p>}
                  {challengeMutation.isSuccess && <p className="collab-bb-success">挑战已记录。</p>}
                  <button className="button-primary" disabled={challengeMutation.isPending || !challengeReason.trim()}>{challengeMutation.isPending ? '提交中…' : '发起挑战'}</button>
                </form>

                {text(selected.kind, '') === 'conflict' && <form className="collab-bb-form" onSubmit={submitDecision}>
                  <h4>提交决策</h4>
                  <label>决策结果<input value={decisionOutcome} onChange={event => setDecisionOutcome(event.target.value)} required placeholder="例如采纳方案 A" /></label>
                  <label>决策依据<textarea rows={3} value={decisionRationale} onChange={event => setDecisionRationale(event.target.value)} required /></label>
                  <label>证据引用（可选，逗号或换行分隔）<textarea rows={2} value={decisionRefs} onChange={event => setDecisionRefs(event.target.value)} /></label>
                  {decisionMutation.isError && <p className="collab-bb-error">{blackboardError(decisionMutation.error)}</p>}
                  {decisionMutation.isSuccess && <p className="collab-bb-success">决策已记录。</p>}
                  <button className="button-primary" disabled={decisionMutation.isPending || !decisionOutcome.trim() || !decisionRationale.trim()}>{decisionMutation.isPending ? '提交中…' : '提交决策'}</button>
                </form>}
              </div>}
            </aside>
          </div>}
      </section>
      )}
    </section>
  )
}
