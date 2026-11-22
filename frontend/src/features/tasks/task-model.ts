/**
 * TaskRecord —— 严格对齐后端 _task_view 真实返回字段。
 * 后端 workbench_tasks.py L41-76 只返回以下字段：
 *   task_id, run_id, agent_id, agent_version, template_id, template_version,
 *   project_id, session_id, status, priority, retry_of_run_id, created_by,
 *   input, output, usage, trace_id, last_error, duration_ms,
 *   created_at, updated_at, started_at, finished_at, checkpoint_ref,
 *   registry_snapshot_digest, dispatch_ready
 * 前端不声明后端永远不返回的字段（title/description/project_name/
 * blocked_reason/tags/participant_agent_ids/artifacts/owner/due_at/progress
 * 等），一律通过 helper 从 input 或其它字段兜底。
 */
export const TASK_LIST_COLUMNS = [
  { key: 'title', label: '任务名称', required: true },
  { key: 'status', label: '状态' },
  { key: 'priority', label: '优先级' },
  { key: 'project', label: '项目' },
  { key: 'agent', label: '负责 Agent' },
  { key: 'creator', label: '创建人' },
  { key: 'created', label: '创建时间' },
  { key: 'updated', label: '更新时间' },
  { key: 'duration', label: '耗时' },
  { key: 'run', label: '最近 Run' },
  { key: 'trace', label: 'Trace ID' },
] as const
export type TaskListColumnKey = (typeof TASK_LIST_COLUMNS)[number]['key']
export const DEFAULT_TASK_LIST_COLUMNS: TaskListColumnKey[] = [
  'title', 'status', 'priority', 'project', 'agent', 'created', 'run',
]
export function normalizeTaskListColumns(value: string | null): TaskListColumnKey[] {
  if (!value) return [...DEFAULT_TASK_LIST_COLUMNS]
  const allowed = new Set<string>(TASK_LIST_COLUMNS.map((column) => column.key))
  const selected = [...new Set(value.split(',').filter((key) => allowed.has(key)))] as TaskListColumnKey[]
  return selected.includes('title') ? selected : ['title', ...selected]
}
export type TaskRecord = {
  // === 后端 _task_view 真实字段 ===
  task_id: string
  run_id?: string
  agent_id?: string
  agent_version?: string
  template_id?: string
  template_version?: string
  project_id?: string
  session_id?: string
  status: string
  priority?: number | string
  retry_of_run_id?: string | null
  created_by?: string | null
  input?: unknown
  output?: unknown
  usage?: unknown
  trace_id?: string | null
  last_error?: string | null
  duration_ms?: number | null
  created_at?: string
  updated_at?: string
  started_at?: string | null
  finished_at?: string | null
  checkpoint_ref?: string | null
  registry_snapshot_digest?: string | null
  dispatch_ready?: boolean
  // === 前端 fallback 别名字段（后端 never 返回，兼容历史代码） ===
  title?: string
  description?: string
  latest_run_id?: string
  assigned_agent_id?: string
  owner?: string
  project_name?: string
  blocked_reason?: string
  tags?: string[]
  participant_agent_ids?: string[]
  run_count?: number
  due_at?: string
  progress?: number
  artifacts?: Array<{ id?: string; name?: string; url?: string }>
  [key: string]: unknown
}
type InputShape = Record<string, unknown>
/** 从 input 字段尽力提取一个可读的任务名。 */
function titleFromInput(input: unknown): string | null {
  if (!input || typeof input !== 'object') return null
  const obj = input as InputShape
  const t = obj.title ?? obj.name ?? obj.task_name
  if (typeof t === 'string' && t.trim()) return t.trim()
  const p = obj.prompt ?? obj.description
  if (typeof p === 'string' && p.trim()) {
    const s = p.trim().replace(/\s+/g, ' ')
    return s.length > 60 ? s.slice(0, 60) + '…' : s
  }
  return null
}
/** 解析任务展示名称：显式 title → input 挖 → task_id 短号兜底 */
export function resolveTaskTitle(t: TaskRecord): string {
  if (t.title && t.title.trim()) return t.title.trim()
  const fromInput = titleFromInput(t.input)
  if (fromInput) return fromInput
  const id = t.task_id || ''
  const last = id.split('-').pop() || id.slice(-8)
  return `任务 ${last}`
}
/** 解析任务描述：input.description → project_name → 空串 */
export function resolveTaskDescription(t: TaskRecord): string {
  if (t.description && t.description.trim()) return t.description.trim()
  if (t.input && typeof t.input === 'object') {
    const obj = t.input as InputShape
    const desc = obj.description
    if (typeof desc === 'string' && desc.trim()) return desc.trim()
    const prompt = obj.prompt
    if (typeof prompt === 'string' && prompt.trim()) {
      const s = prompt.trim().replace(/\s+/g, ' ')
      return s.length > 160 ? s.slice(0, 160) + '…' : s
    }
  }
  return ''
}
/** 解析项目名/ID：project_name（后端可选）→ project_id → 未关联 */
export function resolveProjectLabel(t: TaskRecord): string {
  if (t.project_name && t.project_name.trim()) return t.project_name.trim()
  return t.project_id ?? '未关联项目'
}
/** 解析 Agent ID（后端真实字段是 agent_id，assigned_agent_id 是旧前端别名） */
export function resolveAgentId(t: TaskRecord): string | undefined {
  return (t.agent_id as string | undefined) ?? t.assigned_agent_id
}
/** 解析运行 ID：latest_run_id（旧别名）→ run_id（后端真实字段） */
export function resolveRunId(t: TaskRecord): string | undefined {
  return (t.latest_run_id as string | undefined) ?? t.run_id
}
/**
 * RunRecord —— 对齐后端三个 Run 相关接口的返回：
 *  1) GET /runs/{run_id} — runs.py serialize()
 *  2) GET /workbench/tasks/{task_id}/runs — workbench_tasks.list_task_runs
 *  两者都返回 run_id/status/agent_id/duration_ms/last_error/
 *  created_at/started_at/finished_at/updated_at，
 *  单个 GET 额外返回 task_id/trace_id。
 */
export type RunRecord = {
  run_id: string
  task_id?: string
  agent_id?: string
  trace_id?: string | null
  status: string
  version?: number
  checkpoint_ref?: string | null
  last_error?: string | null
  duration_ms?: number | null
  created_at?: string
  started_at?: string | null
  finished_at?: string | null
  updated_at?: string
  output?: unknown
  input?: unknown
  usage?: unknown
  // 前端历史遗留别名（后端永不返回）
  error_code?: string
  [key: string]: unknown
}

// ---- RunEvent / 通用工具 ----

export type RunEvent = {
  event_id: string
  type: string
  occurred_at?: string
  actor?: string
  source?: string
  request_id?: string
  trace_id?: string
  sequence?: number
  summary?: string
  payload?: unknown
}

export function classifyRunEvents(events: RunEvent[]): {
  steps: RunEvent[]
  logs: RunEvent[]
} {
  const steps: RunEvent[] = []
  const logs: RunEvent[] = []
  for (const event of events) {
    const type = event.type
    const payload =
      event.payload && typeof event.payload === 'object' && !Array.isArray(event.payload)
        ? (event.payload as Record<string, unknown>)
        : null
    if (/(step|node|tool)[._:-]/i.test(type)) steps.push(event)
    if (
      /(log|error|exception)/i.test(type) ||
      Boolean(payload && ['level', 'message', 'log', 'error', 'stack_trace'].some((key) => key in payload))
    ) logs.push(event)
  }
  return { steps, logs }
}
export function unwrapList<T>(v: unknown): T[] {
  if (Array.isArray(v)) return v as T[]
  if (v && typeof v === 'object') {
    const r = v as Record<string, unknown>
    if (Array.isArray(r.data)) return r.data as T[]
    if (Array.isArray(r.items)) return r.items as T[]
    if (
      r.data &&
      typeof r.data === 'object' &&
      Array.isArray((r.data as Record<string, unknown>).items)
    )
      return (r.data as { items: T[] }).items
  }
  return []
}
export function unwrapRecord<T>(v: unknown): T {
  if (v && typeof v === 'object' && 'data' in v) return (v as { data: T }).data
  return v as T
}
export function sortEvents(events: RunEvent[]): RunEvent[] {
  return [...events].sort((a, b) =>
    a.sequence != null && b.sequence != null && a.sequence !== b.sequence
      ? a.sequence - b.sequence
      : (Date.parse(a.occurred_at ?? '') || 0) -
          (Date.parse(b.occurred_at ?? '') || 0) ||
        a.event_id.localeCompare(b.event_id),
  )
}
export function formatDuration(ms?: number | null): string {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return '—'
  const s = Math.floor(ms / 1000)
  return s < 60 ? `${Math.round(ms)} ms` : `${Math.floor(s / 60)}m ${s % 60}s`
}


/** Parse one Server-Sent Events frame from the Run event broker. */
export function parseRunEventFrame(frame: string, runId: string): RunEvent | null {
  let eventId = ''
  let eventName = ''
  const dataLines: string[] = []
  for (const line of frame.split(/\r?\n/)) {
    if (!line || line.startsWith(':')) continue
    const separator = line.indexOf(':')
    const field = separator < 0 ? line : line.slice(0, separator)
    const value = (separator < 0 ? '' : line.slice(separator + 1)).replace(/^ /, '')
    if (field === 'id') eventId = value
    else if (field === 'event') eventName = value
    else if (field === 'data') dataLines.push(value)
  }
  if (!dataLines.length) return null
  let data: Record<string, unknown>
  try {
    const parsed: unknown = JSON.parse(dataLines.join('\n'))
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null
    data = parsed as Record<string, unknown>
  } catch {
    return null
  }
  const sequence = typeof data.sequence === 'number' ? data.sequence : undefined
  const resolvedId = eventId || (typeof data.event_id === 'string' ? data.event_id : sequence != null ? `${runId}:${sequence}` : '')
  const type = typeof data.event_type === 'string' ? data.event_type : typeof data.type === 'string' ? data.type : eventName
  if (!resolvedId || !type) return null
  const occurredAt = data.occurred_at ?? data.timestamp
  return {
    event_id: resolvedId,
    type,
    occurred_at: typeof occurredAt === 'string' ? occurredAt : undefined,
    actor: typeof data.actor === 'string' ? data.actor : typeof data.actor_id === 'string' ? data.actor_id : undefined,
    source: typeof data.source === 'string' ? data.source : undefined,
    request_id: typeof data.request_id === 'string' ? data.request_id : undefined,
    trace_id: typeof data.trace_id === 'string' ? data.trace_id : undefined,
    sequence,
    summary: typeof data.summary === 'string' ? data.summary : typeof data.message === 'string' ? data.message : undefined,
    payload: data.payload ?? data,
  }
}

/** Merge replayed/live events without duplicating IDs; sequence remains authoritative. */
export function mergeRunEvents(existing: RunEvent[], incoming: RunEvent[]): RunEvent[] {
  const byId = new Map<string, RunEvent>()
  for (const event of [...existing, ...incoming]) byId.set(event.event_id, event)
  return sortEvents([...byId.values()])
}
