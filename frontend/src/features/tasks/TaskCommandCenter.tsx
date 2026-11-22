import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, apiClient } from '@/lib/api'
import { EmptyState, ErrorState, LoadingState } from '@/components/States'
import { eventLabel, localizedJson, valueLabel } from '@/lib/i18n/labels'
import {
  formatDuration,
  resolveAgentId,
  resolveProjectLabel,
  resolveRunId,
  resolveTaskDescription,
  resolveTaskTitle,
  sortEvents,
  mergeRunEvents,
  parseRunEventFrame,
  normalizeTaskListColumns,
  TASK_LIST_COLUMNS,
  unwrapList,
  unwrapRecord,
} from './task-model'
import type { RunEvent, RunRecord, TaskRecord, TaskListColumnKey } from './task-model'
import './task-controls.css'
const TASKS = '/platform/v1/workbench/tasks',
  RUNS = '/platform/v1/runs',
  AGENT_REGISTRY = '/platform/v1/registry/agent'
type AgentRegistryItem = {
  item_id: string
  definition?: { name?: string; [k: string]: unknown }
}
/** 从 registry item 里提取 Agent 展示名 */
const agentRegistryName = (r?: AgentRegistryItem) => {
  const name = r?.definition?.name
  return typeof name === 'string' && name.trim() ? name.trim() : null
}
const priority: Record<string, string> = {
  urgent: '紧急',
  high: '高',
  normal: '普通',
  low: '低',
}
/** 状态 → 徽章 CSS 类名，用于列表/看板视觉区分 */
const STATUS_BADGE_CLASS: Record<string, string> = {
  succeeded: 'badge-success',
  success: 'badge-success',
  completed: 'badge-success',
  done: 'badge-success',
  failed: 'badge-failed',
  failure: 'badge-failed',
  error: 'badge-failed',
  timeout: 'badge-failed',
  timed_out: 'badge-failed',
  running: 'badge-running',
  executing: 'badge-running',
  queued: 'badge-pending',
  pending: 'badge-pending',
  waiting_approval: 'badge-waiting',
  waiting_human: 'badge-waiting',
  paused: 'badge-paused',
  blocked: 'badge-blocked',
  cancelled: 'badge-cancelled',
  canceled: 'badge-cancelled',
  cancelling: 'badge-cancelled',
  retrying: 'badge-running',
}
const PAGE_SIZE_OPTIONS = [10, 20, 50, 100]
const DEFAULT_PAGE_SIZE = 20
const dt = (v?: string) => (v ? new Date(v).toLocaleString() : '—')
const err = (e: unknown) => (e instanceof Error ? e.message : '请求失败')
const STATUS_REASONS: Record<number, string> = {
  403: '没有权限执行该操作',
  404: '目标资源不存在或已被移除',
  409: '当前状态不允许该操作',
  503: '服务暂不可用，请稍后重试',
}
const mutErr = (e: unknown) => {
  if (!(e instanceof ApiError)) return err(e)
  const reason = STATUS_REASONS[e.status] ?? '请求失败'
  const detail =
    e.message && !e.message.startsWith('Request failed') ? `：${e.message}` : ''
  return `${reason}（${e.status}）${detail}`
}
const RUN_TERMINAL = new Set(['succeeded', 'failed', 'cancelled', 'timed_out'])
const RUN_CANCELLABLE = new Set(['queued', 'running', 'paused', 'interrupted'])
const TASK_RETRYABLE = new Set(['failed', 'timed_out'])
const KIND_LABELS: Record<string, string> = {
  decision: '人工决策',
  approval: '人工审批',
  context: '上下文补充',
  takeover: '人工接管',
}
const EFFECT_STATES: Record<string, string> = {
  pending: '待确认',
  succeeded: '已成功',
  failed: '已失败',
  retryable: '可重试',
  compensated: '已补偿',
}
const RESOLUTION_LABELS: Record<string, string> = {
  confirmed_succeeded: '确认已生效',
  confirmed_not_applied: '确认未生效（可重试）',
  compensated: '已补偿',
}
const retryKey = () =>
  typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `task-retry-${Date.now().toString(36)}-${Math.random()
        .toString(36)
        .slice(2)}`
type RawRunEvent = RunEvent & { event_type?: string; actor_id?: string }
type InterventionRecord = {
  intervention_id: string
  actor_id?: string | null
  kind: string
  payload?: Record<string, unknown>
  status: string
  created_at?: string
  applied_at?: string | null
  applied_by?: string | null
}
type ToolEffectRecord = {
  run_id: string
  call_id: string
  action_digest?: string
  actor_id?: string
  state: string
  result?: unknown
  error?: string | null
  created_at?: string | null
  updated_at?: string | null
  resolution?: string | null
  reconciled_by?: string | null
  reconciliation_note?: string | null
  reconciled_at?: string | null
}
const eventTypeOf = (e: RawRunEvent) => e.event_type ?? e.type
const payloadOf = (e: RunEvent): Record<string, unknown> | null => {
  const payload = e.payload
  return payload && typeof payload === 'object' && !Array.isArray(payload)
    ? (payload as Record<string, unknown>)
    : null
}
const isPendingDecision = (item: InterventionRecord) =>
  item.status === 'pending' &&
  (item.kind === 'decision' || item.kind === 'approval') &&
  !(
    item.payload &&
    Object.prototype.hasOwnProperty.call(item.payload, 'decision')
  )
function Card({
  t,
  agentNameOf,
}: {
  t: TaskRecord
  agentNameOf: (id?: string) => string | undefined
}) {
  const title = resolveTaskTitle(t)
  const desc = resolveTaskDescription(t)
  const agentId = resolveAgentId(t)
  const agentLabel = agentNameOf(agentId) ?? agentId ?? '未分派'
  const runId = resolveRunId(t)
  return (
    <Link
      className="task-card"
      to={`/operations/tasks/${encodeURIComponent(t.task_id)}`}
    >
      <small>
        {t.task_id.slice(-8)} ·{' '}
        {priority[String(t.priority ?? '')] ?? t.priority ?? '未设优先级'}
      </small>
      <strong>{title}</strong>
      {desc && <p>{desc}</p>}
      <small>
        {resolveProjectLabel(t)} · {agentLabel}
        {t.created_by && <> · 由 {String(t.created_by).slice(0, 8)}</>}
      </small>
      {t.last_error && (
        <p className="task-blocked">错误：{t.last_error}</p>
      )}
      <small>
        <span className={`status-badge ${STATUS_BADGE_CLASS[String(t.status).toLowerCase()] ?? 'badge-default'}`}>
          {valueLabel(t.status)}
        </span>{' '}
        · {runId ? `运行 ${String(runId).slice(-8)}` : '暂无运行'}
      </small>
    </Link>
  )
}
export function TaskCommandCenter() {
  const [sp, setSp] = useSearchParams(),
    // 默认视图改为列表，支持 URL 中已有的 view 参数
    [view, setView] = useState<'kanban' | 'list' | 'timeline'>(
      (sp.get('view') as 'kanban' | 'list' | 'timeline') ?? 'list',
    )
  const visibleColumns = normalizeTaskListColumns(sp.get('columns'))
  const q = useQuery({
    queryKey: ['tasks'],
    queryFn: () =>
      apiClient
        .get<unknown>(TASKS)
        .then((v) => ({ items: unwrapList<TaskRecord>(v), raw: v })),
    refetchInterval: 15000,
  })
  // 并行加载 agent registry，用于 agent_id → 友好名称映射
  const agentsQ = useQuery({
    queryKey: ['registry', 'agent', 'list'],
    queryFn: () =>
      apiClient
        .get<unknown>(AGENT_REGISTRY)
        .then((v) => unwrapList<AgentRegistryItem>(v)),
    refetchInterval: 60000,
  })
  const agentNameMap = useMemo(() => {
    const m = new Map<string, string>()
    for (const a of agentsQ.data ?? []) {
      const name = agentRegistryName(a)
      if (name) m.set(a.item_id, name)
    }
    return m
  }, [agentsQ.data])
  const agentNameOf = (id?: string) =>
    id ? agentNameMap.get(id) : undefined
  const tasks = q.data?.items ?? []
  const statuses = useMemo(
    () => [...new Set(tasks.map((t) => t.status))].sort(),
    [tasks],
  )
  const filtered = tasks.filter(
    (t) =>
      (!sp.get('q') ||
        `${resolveTaskTitle(t)} ${t.task_id} ${t.description ?? ''}`
          .toLowerCase()
          .includes(sp.get('q')!.toLowerCase())) &&
      (!sp.get('status') || t.status === sp.get('status')) &&
      (!sp.get('priority') ||
        String(t.priority ?? '') === sp.get('priority')) &&
      (!sp.get('project_id') || t.project_id === sp.get('project_id')) &&
      (!sp.get('agent_id') || resolveAgentId(t) === sp.get('agent_id')) &&
      (!sp.get('created_by') ||
        String(t.created_by ?? '').toLowerCase().includes(sp.get('created_by')!.toLowerCase())) &&
      (!sp.get('from') ||
        (Boolean(t.created_at) && Date.parse(t.created_at!) >= Date.parse(sp.get('from')!))) &&
      (!sp.get('to') ||
        (Boolean(t.created_at) && Date.parse(t.created_at!) <= Date.parse(sp.get('to')!))) &&
      (!sp.get('tag') || t.tags?.includes(sp.get('tag')!) === true),
  )
  const sortKey = sp.get('sort') ?? 'created_at'
  const sortDirection = sp.get('order') === 'asc' ? 1 : -1
  const sortedFiltered = [...filtered].sort((a, b) => {
    const aValue = sortKey === 'updated_at' ? a.updated_at
      : sortKey === 'duration_ms' ? a.duration_ms
      : a.created_at
    const bValue = sortKey === 'updated_at' ? b.updated_at
      : sortKey === 'duration_ms' ? b.duration_ms
      : b.created_at
    const primary = typeof aValue === 'number' || typeof bValue === 'number'
      ? Number(aValue ?? 0) - Number(bValue ?? 0)
      : String(aValue ?? '').localeCompare(String(bValue ?? ''))
    return primary * sortDirection || a.task_id.localeCompare(b.task_id)
  })
  // 分页：从 URL 读取，非法值回退默认
  const page = Math.max(1, Number(sp.get('page') ?? 1) || 1)
  const pageSize =
    PAGE_SIZE_OPTIONS.includes(Number(sp.get('page_size')))
      ? Number(sp.get('page_size'))
      : DEFAULT_PAGE_SIZE
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize))
  const safePage = Math.min(page, totalPages)
  const startIdx = (safePage - 1) * pageSize
  const paged = sortedFiltered.slice(startIdx, startIdx + pageSize)
  const set = (k: string, v: string) => {
    const n = new URLSearchParams(sp)
    if (v) {
      n.set(k, v)
    } else {
      n.delete(k)
    }
    setSp(n)
  }
  const goPage = (p: number) => {
    const n = new URLSearchParams(sp)
    if (p <= 1) n.delete('page')
    else n.set('page', String(p))
    setSp(n)
  }
  const changePageSize = (s: number) => {
    const n = new URLSearchParams(sp)
    n.delete('page') // 切换 pageSize 时回到第 1 页
    if (s !== DEFAULT_PAGE_SIZE) n.set('page_size', String(s))
    setSp(n)
  }
  const statusBadgeClass = (status: string) =>
    STATUS_BADGE_CLASS[status.toLowerCase()] ?? 'badge-default'
  // 分页按钮：最多显示当前页前后 1 页 + 省略号
  const pageButtons = useMemo(() => {
    const btns: Array<{ type: 'num' | 'ellipsis'; value?: number }> = []
    const push = (v: number) => btns.push({ type: 'num', value: v })
    if (totalPages <= 7) {
      for (let i = 1; i <= totalPages; i++) push(i)
    } else {
      push(1)
      if (safePage > 3) btns.push({ type: 'ellipsis' })
      const from = Math.max(2, safePage - 1)
      const to = Math.min(totalPages - 1, safePage + 1)
      for (let i = from; i <= to; i++) push(i)
      if (safePage < totalPages - 2) btns.push({ type: 'ellipsis' })
      push(totalPages)
    }
    return btns
  }, [safePage, totalPages])
  return (
    <section className="page-content task-center">
      <div className="page-heading task-heading">
        <div>
          <p className="eyebrow">工作空间 / 任务运营</p>
          <h1>任务指挥中心</h1>
          <p>跟踪任务生命周期、负责人、阻塞和交付。</p>
        </div>
        <small>自动刷新 · 15s</small>
      </div>
      <div className="task-toolbar">
        <input
          aria-label="搜索任务"
          placeholder="搜索标题 / ID"
          value={sp.get('q') ?? ''}
          onChange={(e) => set('q', e.target.value)}
        />
        <select
          aria-label="状态筛选"
          value={sp.get('status') ?? ''}
          onChange={(e) => set('status', e.target.value)}
        >
          <option value="">全部状态</option>
          {statuses.map((s) => (
            <option key={s} value={s}>
              {valueLabel(s)}
            </option>
          ))}
        </select>
        <select
          aria-label="优先级筛选"
          value={sp.get('priority') ?? ''}
          onChange={(e) => set('priority', e.target.value)}
        >
          <option value="">全部优先级</option>
          {Object.entries(priority).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
        <input
          aria-label="项目筛选"
          placeholder="项目 ID"
          value={sp.get('project_id') ?? ''}
          onChange={(e) => set('project_id', e.target.value)}
        />
        <input
          aria-label="智能体筛选"
          placeholder="智能体标识"
          value={sp.get('agent_id') ?? ''}
          onChange={(e) => set('agent_id', e.target.value)}
        />
        <input
          aria-label="创建人筛选"
          placeholder="创建人"
          value={sp.get('created_by') ?? ''}
          onChange={(e) => set('created_by', e.target.value)}
        />
        <label className="task-date-filter">
          创建时间起
          <input
            aria-label="创建时间起"
            type="datetime-local"
            value={sp.get('from') ?? ''}
            onChange={(e) => set('from', e.target.value)}
          />
        </label>
        <label className="task-date-filter">
          创建时间止
          <input
            aria-label="创建时间止"
            type="datetime-local"
            value={sp.get('to') ?? ''}
            onChange={(e) => set('to', e.target.value)}
          />
        </label>
        <select
          aria-label="排序字段"
          value={sortKey}
          onChange={(e) => set('sort', e.target.value)}
        >
          <option value="created_at">创建时间</option>
          <option value="updated_at">更新时间</option>
          <option value="duration_ms">运行耗时</option>
        </select>
        <select
          aria-label="排序方向"
          value={sortDirection === 1 ? 'asc' : 'desc'}
          onChange={(e) => set('order', e.target.value)}
        >
          <option value="desc">降序</option>
          <option value="asc">升序</option>
        </select>
        <div className="task-view-switch">
          {(['kanban', 'list', 'timeline'] as const).map((v) => (
            <button
              className={view === v ? 'selected' : ''}
              key={v}
              onClick={() => {
                setView(v)
                // 同步 view 到 URL
                const n = new URLSearchParams(sp)
                if (v === 'list') n.delete('view')
                else n.set('view', v)
                setSp(n)
              }}
            >
              {v === 'kanban' ? '看板' : v === 'list' ? '列表' : '时间线'}
            </button>
          ))}
        </div>
        {view === 'list' && (
          <details className="task-column-config">
            <summary>配置列表列（{visibleColumns.length}/{TASK_LIST_COLUMNS.length}）</summary>
            <div role="group" aria-label="可见任务列表列">
              {TASK_LIST_COLUMNS.map((column) => (
                <label key={column.key}>
                  <input
                    type="checkbox"
                    checked={visibleColumns.includes(column.key)}
                    disabled={column.key === 'title'}
                    onChange={(event) => {
                      const next = event.target.checked
                        ? [...visibleColumns, column.key]
                        : visibleColumns.filter((key) => key !== column.key)
                      const ordered = TASK_LIST_COLUMNS
                        .map((item) => item.key)
                        .filter((key) => next.includes(key))
                      set('columns', ordered.join(','))
                    }}
                  />
                  {column.label}
                </label>
              ))}
              <button type="button" onClick={() => set('columns', '')}>
                恢复默认列
              </button>
            </div>
          </details>
        )}
      </div>
      {q.isPending ? (
        <LoadingState label="正在加载任务" />
      ) : q.isError ? (
        <ErrorState
          title="任务列表加载失败"
          description={err(q.error)}
          onRetry={() => void q.refetch()}
        />
      ) : !filtered.length ? (
        <EmptyState
          title="没有匹配的任务"
          description="调整筛选条件，或等待服务端任务数据。"
        />
      ) : view === 'kanban' ? (
        <div className="task-kanban">
          {statuses.map((s) => (
            <section className="task-column" key={s}>
              <header>
                <strong>{valueLabel(s)}</strong>
                <span>{filtered.filter((t) => t.status === s).length}</span>
              </header>
              {filtered
                .filter((t) => t.status === s)
                .map((t) => (
                  <Card
                    key={t.task_id}
                    t={t}
                    agentNameOf={agentNameOf}
                  />
                ))}
            </section>
          ))}
        </div>
      ) : view === 'list' ? (
        <>
          <div className="task-table-wrap">
            <table className="task-table">
              <thead>
                <tr>
                  {visibleColumns.map((key) => (
                    <th key={key}>
                      {TASK_LIST_COLUMNS.find((column) => column.key === key)?.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {paged.map((t) => {
                  const agentId = resolveAgentId(t)
                  const agentLabel = agentNameOf(agentId) ?? agentId ?? '—'
                  const runId = resolveRunId(t)
                  const cells: Record<TaskListColumnKey, ReactNode> = {
                    title: (
                      <>
                        <Link to={`/operations/tasks/${encodeURIComponent(t.task_id)}`}>
                          {resolveTaskTitle(t)}
                        </Link>
                        <small>{t.task_id}</small>
                      </>
                    ),
                    status: (
                      <span className={`status-badge ${statusBadgeClass(t.status)}`}>
                        {valueLabel(t.status)}
                      </span>
                    ),
                    priority:
                      priority[String(t.priority ?? '')] ?? t.priority ?? '—',
                    project: resolveProjectLabel(t),
                    agent: agentLabel,
                    creator: t.created_by ?? '—',
                    created: dt(t.created_at),
                    updated: dt(t.updated_at),
                    duration: formatDuration(t.duration_ms),
                    run: runId ? (
                      <Link to={`/operations/runs/${encodeURIComponent(runId)}`}>
                        {String(runId).slice(-8)}
                      </Link>
                    ) : (
                      '—'
                    ),
                    trace: t.trace_id ? (
                      runId ? (
                        <Link to={`/operations/runs/${encodeURIComponent(runId)}`}>
                          {t.trace_id}
                        </Link>
                      ) : (
                        t.trace_id
                      )
                    ) : (
                      '—'
                    ),
                  }
                  return (
                    <tr key={t.task_id}>
                      {visibleColumns.map((key) => (
                        <td key={key}>{cells[key]}</td>
                      ))}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <div className="task-pagination">
            <span className="task-pagination-info">
              共 {filtered.length} 条 · 第 {safePage} / {totalPages} 页
              （{startIdx + 1}–{Math.min(startIdx + pageSize, filtered.length)}）
            </span>
            <button
              type="button"
              className="task-page-btn"
              disabled={safePage <= 1}
              onClick={() => goPage(1)}
            >
              首页
            </button>
            <button
              type="button"
              className="task-page-btn"
              disabled={safePage <= 1}
              onClick={() => goPage(safePage - 1)}
            >
              上一页
            </button>
            {pageButtons.map((b, i) =>
              b.type === 'ellipsis' ? (
                <span key={`e-${i}`} className="task-page-ellipsis">
                  …
                </span>
              ) : (
                <button
                  key={b.value}
                  type="button"
                  className={`task-page-btn ${safePage === b.value ? 'active' : ''}`}
                  onClick={() => goPage(b.value!)}
                >
                  {b.value}
                </button>
              ),
            )}
            <button
              type="button"
              className="task-page-btn"
              disabled={safePage >= totalPages}
              onClick={() => goPage(safePage + 1)}
            >
              下一页
            </button>
            <button
              type="button"
              className="task-page-btn"
              disabled={safePage >= totalPages}
              onClick={() => goPage(totalPages)}
            >
              末页
            </button>
            <select
              aria-label="每页条数"
              className="task-page-size"
              value={pageSize}
              onChange={(e) => changePageSize(Number(e.target.value))}
            >
              {PAGE_SIZE_OPTIONS.map((s) => (
                <option key={s} value={s}>
                  {s} 条/页
                </option>
              ))}
            </select>
          </div>
        </>
      ) : (
        <div className="task-timeline">
          {filtered
            .filter((t) => t.created_at)
            .sort(
              (a, b) => Date.parse(a.created_at!) - Date.parse(b.created_at!),
            )
            .map((t) => {
              const agentId = resolveAgentId(t)
              const agentLabel = agentNameOf(agentId) ?? agentId ?? '未分派'
              return (
                <article key={t.task_id}>
                  <time>{dt(t.created_at)}</time>
                  <div>
                    <Link
                      to={`/operations/tasks/${encodeURIComponent(t.task_id)}`}
                    >
                      {resolveTaskTitle(t)}
                    </Link>
                    <span>
                      <span
                        className={`status-badge ${statusBadgeClass(t.status)}`}
                      >
                        {valueLabel(t.status)}
                      </span>{' '}
                      · {agentLabel}
                    </span>
                  </div>
                </article>
              )
            })}
        </div>
      )}
      <p className="task-data-note">
        状态列完全来自服务端枚举；无可信依赖与截止时间字段时不显示
        Gantt。写操作仅限任务详情页的取消与重试。
      </p>
    </section>
  )
}
export function TaskDetailPage() {
  const { taskId = '' } = useParams()
  const qc = useQueryClient()
  const tq = useQuery({
    queryKey: ['task', taskId],
    queryFn: () =>
      apiClient
        .get<unknown>(`${TASKS}/${encodeURIComponent(taskId)}`)
        .then(unwrapRecord<TaskRecord>),
  })
  const rq = useQuery({
    queryKey: ['task-runs', taskId],
    queryFn: () =>
      apiClient
        .get<unknown>(`${TASKS}/${encodeURIComponent(taskId)}/runs`)
        .then(unwrapList<RunRecord>),
  })
  // Agent registry 用于展示 Agent 友好名称
  const agentsQ = useQuery({
    queryKey: ['registry', 'agent', 'list'],
    queryFn: () =>
      apiClient
        .get<unknown>(AGENT_REGISTRY)
        .then((v) => unwrapList<AgentRegistryItem>(v)),
    refetchInterval: 60000,
  })
  const agentNameMap = useMemo(() => {
    const m = new Map<string, string>()
    for (const a of agentsQ.data ?? []) {
      const name = agentRegistryName(a)
      if (name) m.set(a.item_id, name)
    }
    return m
  }, [agentsQ.data])
  const refresh = async () => {
    await Promise.all([
      qc.invalidateQueries({ queryKey: ['task', taskId] }),
      qc.invalidateQueries({ queryKey: ['task-runs', taskId] }),
      qc.invalidateQueries({ queryKey: ['tasks'] }),
    ])
  }
  const cancelM = useMutation({
    mutationFn: () =>
      apiClient.post(`${TASKS}/${encodeURIComponent(taskId)}/cancel`),
    onSuccess: refresh,
  })
  const retryM = useMutation({
    mutationFn: () =>
      apiClient.post(`${TASKS}/${encodeURIComponent(taskId)}/retry`, {
        idempotency_key: retryKey(),
      }),
    onSuccess: refresh,
  })
  if (tq.isPending) return <LoadingState />
  if (tq.isError)
    return (
      <section className="page-content">
        <ErrorState
          title="任务详情加载失败"
          description={err(tq.error)}
          onRetry={() => void tq.refetch()}
        />
      </section>
    )
  const t = tq.data
  const cancelable = !RUN_TERMINAL.has(t.status)
  const retryable = TASK_RETRYABLE.has(t.status)
  const agentId = resolveAgentId(t)
  const agentDisplay =
    (agentId && (agentNameMap.get(agentId) ?? agentId)) ?? '—'
  const resolvedTitle = resolveTaskTitle(t)
  return (
    <section className="page-content task-detail">
      <Link to="/workspace/tasks">← 返回任务中心</Link>
      <div className="page-heading">
        <p className="eyebrow">任务 / {t.task_id}</p>
        <h1>{resolvedTitle}</h1>
        <p>{t.description ?? '无描述'}</p>
      </div>
      <div className="detail-actions task-actions">
        <button
          className="button-secondary"
          type="button"
          disabled={!cancelable || cancelM.isPending}
          onClick={() => cancelM.mutate()}
        >
          {cancelM.isPending ? '取消中…' : '取消任务'}
        </button>
        <button
          className="button-primary"
          type="button"
          disabled={!retryable || retryM.isPending}
          onClick={() => retryM.mutate()}
        >
          {retryM.isPending ? '重试中…' : '重试任务'}
        </button>
        {cancelM.isSuccess && (
          <span className="run-action-ok">已请求取消任务。</span>
        )}
        {retryM.isSuccess && (
          <span className="run-action-ok">
            已提交重试，新的运行记录已创建。
          </span>
        )}
        {cancelM.isError && (
          <span className="auth-error">{mutErr(cancelM.error)}</span>
        )}
        {retryM.isError && (
          <span className="auth-error">{mutErr(retryM.error)}</span>
        )}
      </div>
      <p className="run-action-hint">
        取消仅对未进入终态的任务可用；重试仅适用于失败或超时的任务。
      </p>
      <section className="task-detail-panel">
        <h2>任务信息</h2>
        <dl>
          {[
            ['状态', valueLabel(t.status)],
            ['优先级', priority[String(t.priority ?? '')] ?? t.priority ?? '—'],
            ['项目', resolveProjectLabel(t)],
            ['负责 Agent', agentDisplay],
            ['创建人', t.created_by ?? '—'],
            ['Trace ID', t.trace_id ?? '—'],
            ['最后错误', t.last_error ?? '—'],
            ['耗时', formatDuration(t.duration_ms)],
            ['创建时间', dt(t.created_at)],
            ['更新时间', dt(t.updated_at)],
          ].map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
        <h3>输入 / 输出</h3>
        <pre>
          {localizedJson({
            input: t.input ?? null,
            output: t.output ?? null,
          })}
        </pre>
      </section>
      <section className="task-detail-panel">
        <h2>运行列表</h2>
        {rq.isPending ? (
          <LoadingState />
        ) : rq.isError ? (
          <ErrorState
            description={mutErr(rq.error)}
            onRetry={() => void rq.refetch()}
          />
        ) : rq.data?.length ? (
          rq.data.map((r) => (
            <Link
              className="run-row"
              key={r.run_id}
              to={`/operations/runs/${encodeURIComponent(r.run_id)}`}
            >
              {r.run_id} · {valueLabel(r.status)} · {dt(r.started_at ?? undefined)} ·{' '}
              {formatDuration(r.duration_ms)} → 追踪
            </Link>
          ))
        ) : (
          <EmptyState title="暂无运行" description="服务端没有返回执行记录。" />
        )}
      </section>
      {t.artifacts?.length ? (
        <section className="task-detail-panel">
          <h2>交付物</h2>
          {t.artifacts.map((a, i) => (
            <p key={a.id ?? i}>
              {a.url ? (
                <a href={a.url} target="_blank" rel="noreferrer">
                  {a.name ?? a.id ?? '打开产物'}
                </a>
              ) : (
                (a.name ?? a.id)
              )}
            </p>
          ))}
        </section>
      ) : null}
      <p className="task-data-note">
        任务写操作直接调用服务端任务接口；按钮是否可用由服务端状态枚举决定。
      </p>
    </section>
  )
}
function InterventionItem({
  runId,
  item,
  onChanged,
}: {
  runId: string
  item: InterventionRecord
  onChanged: () => void
}) {
  const [rationale, setRationale] = useState('')
  const m = useMutation({
    mutationFn: (decision: 'approved' | 'rejected') => {
      if (!rationale.trim()) throw new Error('请填写决定理由')
      return apiClient.post(
        `${RUNS}/${encodeURIComponent(runId)}/interventions/${encodeURIComponent(
          item.intervention_id,
        )}/decision`,
        { decision, rationale: rationale.trim() },
      )
    },
    onSuccess: () => {
      setRationale('')
      onChanged()
    },
  })
  return (
    <li className="intervention-item">
      <div className="intervention-meta">
        <strong>{KIND_LABELS[item.kind] ?? item.kind}</strong>
        <span>提交人 {item.actor_id ?? '—'}</span>
        <span>{dt(item.created_at)}</span>
        <span>ID {item.intervention_id}</span>
      </div>
      {item.payload && <pre>{localizedJson(item.payload)}</pre>}
      <textarea
        aria-label="决定理由"
        rows={2}
        placeholder="填写批准或拒绝的理由（写入运行审计事件）"
        value={rationale}
        onChange={(e) => setRationale(e.target.value)}
      />
      <div className="intervention-actions">
        <button
          className="button-primary"
          type="button"
          disabled={m.isPending}
          onClick={() => m.mutate('approved')}
        >
          {m.isPending && m.variables === 'approved' ? '提交中…' : '批准'}
        </button>
        <button
          className="button-secondary"
          type="button"
          disabled={m.isPending}
          onClick={() => m.mutate('rejected')}
        >
          {m.isPending && m.variables === 'rejected' ? '提交中…' : '拒绝'}
        </button>
        {m.isSuccess && (
          <span className="run-action-ok">决定已提交，等待引擎应用。</span>
        )}
        {m.isError && <span className="auth-error">{mutErr(m.error)}</span>}
      </div>
    </li>
  )
}
function ToolEffectCard({
  runId,
  effect,
  onChanged,
}: {
  runId: string
  effect: ToolEffectRecord
  onChanged: () => void
}) {
  const [resolution, setResolution] = useState('confirmed_succeeded')
  const [note, setNote] = useState('')
  const [resultText, setResultText] = useState('')
  const m = useMutation({
    mutationFn: () => {
      if (!note.trim()) throw new Error('请填写对账说明')
      const body: Record<string, unknown> = {
        resolution,
        note: note.trim(),
      }
      if (resolution === 'confirmed_succeeded') {
        if (!resultText.trim())
          throw new Error('确认已生效时必须填写外部观测到的结果 JSON')
        try {
          body.result = JSON.parse(resultText) as unknown
        } catch {
          throw new Error('结果必须是合法 JSON')
        }
        if (body.result == null) throw new Error('结果不能为 null')
      }
      return apiClient.post(
        `${RUNS}/${encodeURIComponent(runId)}/tool-effects/${encodeURIComponent(
          effect.call_id,
        )}/reconcile`,
        body,
      )
    },
    onSuccess: () => {
      setNote('')
      setResultText('')
      onChanged()
    },
  })
  const reconcilable = effect.state === 'pending' || effect.state === 'failed'
  return (
    <article className="effect-item">
      <div className="effect-meta">
        <strong>{effect.call_id}</strong>
        <span>
          状态：{EFFECT_STATES[effect.state] ?? valueLabel(effect.state)}
        </span>
        {effect.resolution && (
          <span>
            对账结论：
            {RESOLUTION_LABELS[effect.resolution] ?? effect.resolution}
          </span>
        )}
        <span>更新 {dt(effect.updated_at ?? undefined)}</span>
      </div>
      {effect.result != null && (
        <details>
          <summary>已登记结果</summary>
          <pre>{localizedJson(effect.result)}</pre>
        </details>
      )}
      {effect.error && <p className="run-error-text">错误：{effect.error}</p>}
      {effect.reconciliation_note && (
        <p>对账说明：{effect.reconciliation_note}</p>
      )}
      {effect.reconciled_by && (
        <small>
          对账人 {effect.reconciled_by} ·{' '}
          {dt(effect.reconciled_at ?? undefined)}
        </small>
      )}
      {reconcilable ? (
        <div className="effect-reconcile">
          <select
            aria-label="对账结论"
            value={resolution}
            onChange={(e) => setResolution(e.target.value)}
          >
            {Object.entries(RESOLUTION_LABELS).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
          <textarea
            aria-label="对账说明"
            rows={2}
            placeholder="填写对账依据，例如核对第三方系统的最终状态"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          {resolution === 'confirmed_succeeded' && (
            <textarea
              aria-label="外部观测结果"
              rows={2}
              placeholder='外部观测到的结果 JSON，例如 {"status":"ok"}'
              value={resultText}
              onChange={(e) => setResultText(e.target.value)}
            />
          )}
          <div className="intervention-actions">
            <button
              className="button-secondary"
              type="button"
              disabled={m.isPending}
              onClick={() => m.mutate()}
            >
              {m.isPending ? '提交中…' : '提交对账'}
            </button>
            {m.isError && <span className="auth-error">{mutErr(m.error)}</span>}
          </div>
        </div>
      ) : (
        <small>该副作用已完成对账或无需对账。</small>
      )}
    </article>
  )
}
export function RunTracePage() {
  const { runId = '' } = useParams(),
    nav = useNavigate(),
    qc = useQueryClient()
  const [confirmCancel, setConfirmCancel] = useState(false)
  const [manualCallId, setManualCallId] = useState('')
  const [extraCallIds, setExtraCallIds] = useState<string[]>([])
  const [eventPage, setEventPage] = useState(0)
  const [eventTypeFilter, setEventTypeFilter] = useState('all')
  const [eventSearch, setEventSearch] = useState('')
  const EVENT_PAGE_SIZE = 10
  const rq = useQuery({
    queryKey: ['run', runId],
    queryFn: () =>
      apiClient
        .get<unknown>(`${RUNS}/${encodeURIComponent(runId)}`)
        .then(unwrapRecord<RunRecord>),
  })
  const [eventStreamState, setEventStreamState] = useState<'connecting' | 'connected' | 'reconnecting' | 'fallback'>('connecting')
  const eq = useQuery({
    queryKey: ['run-events', runId],
    queryFn: async () => {
      const snapshot = await apiClient
        .get<unknown>(`${RUNS}/${encodeURIComponent(runId)}/events`)
        .then(unwrapList<RunEvent>)
      return mergeRunEvents(qc.getQueryData<RunEvent[]>(['run-events', runId]) ?? [], snapshot)
    },
    refetchInterval: 5000,
  })
  const iq = useQuery({
    queryKey: ['run-interventions', runId],
    queryFn: () =>
      apiClient
        .get<unknown>(`${RUNS}/${encodeURIComponent(runId)}/interventions`)
        .then(unwrapList<InterventionRecord>),
    refetchInterval: 10000,
    retry: false,
  })
  const events = useMemo(
    () => sortEvents(eq.data ?? []) as RawRunEvent[],
    [eq.data],
  )
  // The Run stream supports Last-Event-ID replay. Reconnect from the last received
  // cursor; if the bounded replay window expired, clear the cursor and resync from
  // the durable events endpoint. The 5s query remains a compatibility fallback.
  useEffect(() => {
    if (!runId) return
    let disposed = false
    let lastEventId: string | undefined
    let retryDelay = 1000
    const controller = new AbortController()
    const wait = (ms: number) => new Promise<void>((resolve) => {
      const timeout = setTimeout(resolve, ms)
      controller.signal.addEventListener('abort', () => {
        clearTimeout(timeout)
        resolve()
      }, { once: true })
    })
    const connect = async () => {
      while (!disposed) {
        try {
          setEventStreamState(lastEventId ? 'reconnecting' : 'connecting')
          const headers = new Headers({ Accept: 'text/event-stream' })
          if (lastEventId) headers.set('Last-Event-ID', lastEventId)
          const response = await fetch(`/api${RUNS}/${encodeURIComponent(runId)}/stream`, {
            method: 'GET',
            headers,
            credentials: 'same-origin',
            signal: controller.signal,
          })
          if (response.status === 400 && lastEventId) {
            // Cursor fell outside the broker's retained replay window.
            lastEventId = undefined
            setEventStreamState('reconnecting')
            await qc.invalidateQueries({ queryKey: ['run-events', runId] })
            continue
          }
          if (!response.ok || !response.body) {
            setEventStreamState('fallback')
            await wait(retryDelay)
            retryDelay = Math.min(retryDelay * 2, 15000)
            continue
          }
          setEventStreamState('connected')
          retryDelay = 1000
          const reader = response.body.getReader()
          const decoder = new TextDecoder()
          let buffer = ''
          let shouldReconnect = true
          while (!disposed) {
            const { value, done } = await reader.read()
            if (done) break
            buffer += decoder.decode(value, { stream: true })
            const frames = buffer.split(/\r?\n\r?\n/)
            buffer = frames.pop() ?? ''
            for (const frame of frames) {
              const parsed = parseRunEventFrame(frame, runId)
              if (!parsed) continue
              const idLine = frame.split(/\r?\n/).find((line) => line.startsWith('id:'))
              if (idLine) lastEventId = idLine.slice(3).trim()
              else if (parsed.event_id.includes(':')) lastEventId = parsed.event_id
              if (parsed.type === 'RECONNECT_REQUIRED') {
                shouldReconnect = true
                break
              }
              qc.setQueryData<RunEvent[]>(['run-events', runId], (current) =>
                mergeRunEvents(current ?? [], [parsed]),
              )
              if (['COMPLETE', 'ERROR'].includes(parsed.type)) shouldReconnect = false
            }
            if (!shouldReconnect) break
          }
          await reader.cancel().catch(() => undefined)
          if (!shouldReconnect) return
        } catch {
          if (disposed || controller.signal.aborted) return
          setEventStreamState('reconnecting')
        }
        await wait(retryDelay)
        retryDelay = Math.min(retryDelay * 2, 15000)
      }
    }
    void connect()
    return () => {
      disposed = true
      controller.abort()
    }
  }, [runId, qc])
  // 切换运行时回到第一页事件
  useEffect(() => setEventPage(0), [runId])
  const pendingInterventions = useMemo(
    () => (iq.data ?? []).filter(isPendingDecision),
    [iq.data],
  )
  const revisions = useMemo(
    () => events.filter((e) => eventTypeOf(e) === 'human.result_revised'),
    [events],
  )
  const callIds = useMemo(() => {
    const ids = new Set<string>(extraCallIds)
    for (const e of events) {
      const payload = payloadOf(e)
      if (!payload) continue
      for (const key of ['call_id', 'tool_call_id']) {
        const value = payload[key]
        if (typeof value === 'string' && value) ids.add(value)
      }
    }
    return [...ids]
  }, [events, extraCallIds])
  const teq = useQuery({
    queryKey: ['run-tool-effects', runId, callIds.join('|')],
    enabled: callIds.length > 0,
    retry: false,
    queryFn: async () => {
      const rows = await Promise.all(
        callIds.map(async (callId) => {
          try {
            return await apiClient.get<ToolEffectRecord>(
              `${RUNS}/${encodeURIComponent(runId)}/tool-effects/${encodeURIComponent(
                callId,
              )}`,
            )
          } catch (e) {
            if (e instanceof ApiError && e.status === 404) return null
            throw e
          }
        }),
      )
      return rows.filter((row): row is ToolEffectRecord => row !== null)
    },
  })
  const refreshRun = async () => {
    await Promise.all([
      qc.invalidateQueries({ queryKey: ['run', runId] }),
      qc.invalidateQueries({ queryKey: ['run-events', runId] }),
      qc.invalidateQueries({ queryKey: ['run-interventions', runId] }),
    ])
  }
  const pauseM = useMutation({
    mutationFn: () =>
      apiClient.post(`${RUNS}/${encodeURIComponent(runId)}/pause`),
    onSuccess: refreshRun,
  })
  const resumeM = useMutation({
    mutationFn: () =>
      apiClient.post(`${RUNS}/${encodeURIComponent(runId)}/resume`),
    onSuccess: refreshRun,
  })
  const cancelM = useMutation({
    mutationFn: () =>
      apiClient.post(`${RUNS}/${encodeURIComponent(runId)}/cancel`),
    onSuccess: async () => {
      setConfirmCancel(false)
      await refreshRun()
    },
  })
  if (rq.isPending) return <LoadingState />
  if (rq.isError)
    return (
      <section className="page-content">
        <ErrorState
          title="运行追踪加载失败"
          description={mutErr(rq.error)}
          onRetry={() => void rq.refetch()}
        />
      </section>
    )
  const r = rq.data
  const durationText = (ms: number) =>
    !Number.isFinite(ms) || ms < 0
      ? '—'
      : ms < 1000
        ? `${Math.round(ms)} 毫秒`
        : ms < 60000
          ? `${(ms / 1000).toFixed(1)} 秒`
          : `${Math.floor(ms / 60000)} 分 ${Math.round((ms % 60000) / 1000)} 秒`
  const runDurationMs =
    typeof r.duration_ms === 'number'
      ? r.duration_ms
      : Date.parse(String(r.updated_at ?? r.finished_at ?? '')) -
        Date.parse(String(r.created_at ?? r.started_at ?? ''))
  const eventTypes = [...new Set(events.map((event) => eventTypeOf(event)).filter(Boolean))].sort()
  const filteredEvents = events.filter((event) => {
    const type = eventTypeOf(event)
    if (eventTypeFilter !== 'all' && type !== eventTypeFilter) return false
    const needle = eventSearch.trim().toLocaleLowerCase()
    if (!needle) return true
    const searchable = [type, event.summary, event.actor, event.actor_id, event.source,
      event.request_id, event.trace_id, event.event_id]
      .filter((value): value is string => typeof value === 'string')
      .join(' ').toLocaleLowerCase()
    return searchable.includes(needle)
  })
  const eventTotalPages = Math.max(1, Math.ceil(filteredEvents.length / EVENT_PAGE_SIZE))
  const eventCurrentPage = Math.min(eventPage, eventTotalPages - 1)
  const visibleEvents = filteredEvents.slice(
    eventCurrentPage * EVENT_PAGE_SIZE,
    (eventCurrentPage + 1) * EVENT_PAGE_SIZE,
  )
  return (
    <section className="page-content task-detail">
      <button className="button-link" onClick={() => nav(-1)}>
        ← 返回
      </button>
      <div className="page-heading run-page-heading">
        <p className="eyebrow">运行追踪 · {r.run_id.slice(0, 8)}</p>
        <h1>运行执行时间线</h1>
        <div className="run-heading-meta">
          <span className={`run-chip s-${r.status}`}>{valueLabel(r.status)}</span>
          <span>开始 {dt(r.created_at ?? r.started_at ?? undefined)}</span>
          <span>更新 {dt(r.updated_at ?? r.finished_at ?? undefined)}</span>
          <span>耗时 {durationText(runDurationMs)}</span>
          <span>{events.length} 条事件</span>
        </div>
      </div>
      <section className="task-detail-panel">
        <div className="run-controls-head">
          <h2>事件时间线</h2>
          <span>事件流：{eventStreamState === 'connected' ? '实时连接' : eventStreamState === 'fallback' ? '轮询兼容模式' : '正在恢复'} · {filteredEvents.length}/{events.length} 条匹配</span>
        </div>
        <div className="run-event-filters" role="search" aria-label="筛选运行事件">
          <label>
            <span>事件类型</span>
            <select aria-label="按事件类型筛选" value={eventTypeFilter} onChange={(event) => { setEventTypeFilter(event.target.value); setEventPage(0) }}>
              <option value="all">全部类型</option>
              {eventTypes.map((type) => <option key={type} value={type}>{eventLabel(type)} ({type})</option>)}
            </select>
          </label>
          <label>
            <span>关键词</span>
            <input aria-label="搜索运行事件" type="search" placeholder="事件、摘要、操作人、Trace ID…" value={eventSearch} onChange={(event) => { setEventSearch(event.target.value); setEventPage(0) }} />
          </label>
          {(eventTypeFilter !== 'all' || eventSearch) && <button className="button-secondary" type="button" onClick={() => { setEventTypeFilter('all'); setEventSearch(''); setEventPage(0) }}>清除筛选</button>}
        </div>
        {eq.isPending ? (
          <LoadingState />
        ) : eq.isError ? (
          <ErrorState
            description={mutErr(eq.error)}
            onRetry={() => void eq.refetch()}
          />
        ) : filteredEvents.length ? (
          <>
            <ol className="run-timeline">
              {visibleEvents.map((e, index) => {
                const et = eventTypeOf(e)
                const payload = payloadOf(e)
                const from = typeof payload?.from_status === 'string' ? payload.from_status : ''
                const to = typeof payload?.to_status === 'string' ? payload.to_status : ''
                const globalIndex = eventCurrentPage * EVENT_PAGE_SIZE + index
                const previous = Date.parse(String(filteredEvents[globalIndex - 1]?.occurred_at ?? ''))
                const current = Date.parse(String(e.occurred_at ?? ''))
                const delta = Number.isFinite(previous) && Number.isFinite(current) && current >= previous ? current - previous : NaN
                return (
                  <li key={e.event_id} data-event-type={et}>
                    <span className="run-tl-dot" aria-hidden="true" />
                    <div className="run-tl-body">
                      <div className="run-tl-head">
                        <strong className="run-tl-type">{eventLabel(et)}</strong>
                        <span className="run-tl-time-group">
                          <time>{dt(e.occurred_at)}</time>
                          {Number.isFinite(delta) && <span className="run-tl-delta">+{durationText(delta)}</span>}
                        </span>
                      </div>
                      {from && to ? (
                        <p className="run-tl-flow">
                          <span className={`run-chip s-${from}`}>{valueLabel(from)}</span>
                          <span className="run-tl-arrow">→</span>
                          <span className={`run-chip s-${to}`}>{valueLabel(to)}</span>
                        </p>
                      ) : (
                        <p>{e.summary ?? '无摘要'}</p>
                      )}
                      <small className="run-tl-meta">
                        {e.sequence != null && <>#{e.sequence} · </>}
                        {e.actor ?? e.actor_id ?? e.source ?? '未知来源'}
                      </small>
                      {e.payload != null && (
                        <details className="run-tl-payload">
                          <summary>事件载荷</summary>
                          <pre>{localizedJson(e.payload)}</pre>
                        </details>
                      )}
                    </div>
                  </li>
                )
              })}
            </ol>
            {filteredEvents.length > EVENT_PAGE_SIZE && (
              <div className="run-pagination">
                <button className="button-secondary" type="button" disabled={eventCurrentPage === 0} onClick={() => setEventPage(page => Math.max(0, page - 1))}>← 上一页</button>
                <span>第 {eventCurrentPage + 1} / {eventTotalPages} 页 · 共 {filteredEvents.length} 条</span>
                <button className="button-secondary" type="button" disabled={eventCurrentPage + 1 >= eventTotalPages} onClick={() => setEventPage(page => page + 1)}>下一页 →</button>
              </div>
            )}
          </>
        ) : (
          <EmptyState
            title={events.length ? '没有匹配的运行事件' : '暂无执行事件'}
            description={events.length ? '调整事件类型或关键词筛选条件。' : '服务端没有返回运行事件。'}
          />
        )}
      </section>
      <section className="task-detail-panel run-controls">
        <div className="run-controls-head">
          <h2>运行控制</h2>
          <span>当前状态：{valueLabel(r.status)}</span>
        </div>
        <div className="run-action-row">
          <button
            className="button-secondary"
            type="button"
            disabled={r.status !== 'running' || pauseM.isPending}
            onClick={() => pauseM.mutate()}
          >
            {pauseM.isPending ? '暂停中…' : '暂停'}
          </button>
          <button
            className="button-secondary"
            type="button"
            disabled={r.status !== 'paused' || resumeM.isPending}
            onClick={() => resumeM.mutate()}
          >
            {resumeM.isPending ? '恢复中…' : '恢复'}
          </button>
          <button
            className="button-secondary run-danger"
            type="button"
            disabled={!RUN_CANCELLABLE.has(r.status) || cancelM.isPending}
            onClick={() => setConfirmCancel(true)}
          >
            取消运行
          </button>
          {confirmCancel && (
            <span className="run-confirm">
              确认取消该运行？取消后不可恢复。
              <button
                className="button-primary"
                type="button"
                disabled={cancelM.isPending}
                onClick={() => cancelM.mutate()}
              >
                {cancelM.isPending ? '取消中…' : '确认取消'}
              </button>
              <button
                className="button-secondary"
                type="button"
                disabled={cancelM.isPending}
                onClick={() => setConfirmCancel(false)}
              >
                返回
              </button>
            </span>
          )}
        </div>
        <p className="run-action-hint">
          暂停仅适用于运行中；恢复仅适用于已暂停且存在检查点的运行；终态与取消中的运行不可再次取消。
        </p>
        {pauseM.isSuccess && (
          <span className="run-action-ok">已请求暂停，状态将随后刷新。</span>
        )}
        {resumeM.isSuccess && (
          <span className="run-action-ok">已请求恢复，状态将随后刷新。</span>
        )}
        {cancelM.isSuccess && (
          <span className="run-action-ok">已请求取消，状态将随后刷新。</span>
        )}
        {pauseM.isError && (
          <span className="auth-error">{mutErr(pauseM.error)}</span>
        )}
        {resumeM.isError && (
          <span className="auth-error">{mutErr(resumeM.error)}</span>
        )}
        {cancelM.isError && (
          <span className="auth-error">{mutErr(cancelM.error)}</span>
        )}
      </section>
      <section className="task-detail-panel">
        <h2>人工介入</h2>
        {iq.isPending ? (
          <LoadingState label="正在加载人工介入" />
        ) : iq.isError ? (
          <ErrorState
            description={mutErr(iq.error)}
            onRetry={() => void iq.refetch()}
          />
        ) : pendingInterventions.length ? (
          <ul className="intervention-list">
            {pendingInterventions.map((item) => (
              <InterventionItem
                key={item.intervention_id}
                runId={runId}
                item={item}
                onChanged={() => void refreshRun()}
              />
            ))}
          </ul>
        ) : (
          <EmptyState
            title="暂无待处理介入"
            description="该运行没有等待人工决策或审批的介入项。"
          />
        )}
      </section>
      <section className="task-detail-panel">
        <h2>结果修订</h2>
        {eq.isPending ? (
          <LoadingState />
        ) : revisions.length ? (
          <ol className="revision-list">
            {revisions.map((e) => {
              const payload = payloadOf(e)
              const reason =
                typeof payload?.reason === 'string' && payload.reason
                  ? payload.reason
                  : '未提供'
              const actor =
                typeof payload?.revised_by === 'string'
                  ? payload.revised_by
                  : (e.actor_id ?? e.actor ?? '未知操作人')
              return (
                <li className="revision-item" key={e.event_id}>
                  <div className="intervention-meta">
                    <strong>{actor}</strong>
                    <span>{dt(e.occurred_at)}</span>
                  </div>
                  <p>修订原因：{reason}</p>
                  {payload?.previous_output != null && (
                    <details>
                      <summary>修订前输出</summary>
                      <pre>{localizedJson(payload.previous_output)}</pre>
                    </details>
                  )}
                  {payload?.revised_output != null && (
                    <details>
                      <summary>修订后输出</summary>
                      <pre>{localizedJson(payload.revised_output)}</pre>
                    </details>
                  )}
                </li>
              )
            })}
          </ol>
        ) : (
          <EmptyState
            title="暂无结果修订"
            description="运行事件中没有人工结果修订记录。"
          />
        )}
      </section>
      <section className="task-detail-panel">
        <h2>工具副作用</h2>
        <div className="tool-effect-lookup">
          <input
            aria-label="工具调用 ID"
            placeholder="输入 call_id 查询工具副作用"
            value={manualCallId}
            onChange={(e) => setManualCallId(e.target.value)}
          />
          <button
            className="button-secondary"
            type="button"
            disabled={!manualCallId.trim()}
            onClick={() => {
              const id = manualCallId.trim()
              if (!id) return
              setExtraCallIds((prev) =>
                prev.includes(id) ? prev : [...prev, id],
              )
              setManualCallId('')
            }}
          >
            查询
          </button>
        </div>
        {!callIds.length ? (
          <EmptyState
            title="暂无工具副作用记录"
            description="服务端未提供副作用列表接口；可从运行事件载荷自动识别调用 ID，或在上方输入 call_id 查询。"
          />
        ) : teq.isPending ? (
          <LoadingState label="正在加载工具副作用" />
        ) : teq.isError ? (
          <ErrorState
            description={mutErr(teq.error)}
            onRetry={() => void teq.refetch()}
          />
        ) : teq.data?.length ? (
          <div className="effect-list">
            {teq.data.map((effect) => (
              <ToolEffectCard
                key={effect.call_id}
                runId={runId}
                effect={effect}
                onChanged={() => void teq.refetch()}
              />
            ))}
          </div>
        ) : (
          <EmptyState
            title="未找到工具副作用"
            description="这些调用 ID 在该运行下没有副作用记录。"
          />
        )}
      </section>
      <p className="task-data-note">
        事件按 sequence
        优先、时间次之排序；当前未声明服务端游标补偿能力，不承诺断线无损恢复。
      </p>
    </section>
  )
}
