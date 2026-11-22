import React, { useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, apiClient } from '@/lib/api'
import { EmptyState, ErrorState, LoadingState } from '@/components/States'
import { eventLabel, localizedJson, valueLabel } from '@/lib/i18n/labels'
import { useAuth } from '@/providers/AuthProvider'
import { AgentTeamGraph } from './AgentTeamGraph'
import { WorkflowRunCanvas } from './WorkflowRunCanvas'
import { mergeRunRuntimeEvents, normalizeRunSseEvent } from './workflow-run-events'
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

/** 状态 → CSS 类（小圆点/徽章颜色） */
const statusClass = (status: unknown): string => {
  const s = String(status ?? '').toLowerCase().trim()
  if (!s) return 'event-generic'
  if (['queued', 'pending', 'enqueued'].includes(s)) return 'status-queued'
  if (['running', 'in_progress', 'executing'].includes(s)) return 'status-running'
  if (['succeeded', 'success', 'completed', 'done'].includes(s)) return 'status-succeeded'
  if (['failed', 'error', 'errored'].includes(s)) return 'status-failed'
  if (['cancelled', 'canceled', 'aborted'].includes(s)) return 'status-cancelled'
  return 'event-generic'
}
/** 从 payload 里抽出状态变化（from_status → to_status） */
const extractStatusFlow = (payload: unknown): { from?: string; to?: string } => {
  if (!payload || typeof payload !== 'object') return {}
  const p = payload as Record<string, unknown>
  return { from: text(p.from_status ?? p.fromState ?? p.from, ''), to: text(p.to_status ?? p.toState ?? p.to, '') }
}
/** 从事件集合里统计状态流转次数，返回最常见路径 */
const summarizeStatusFlows = (evs: unknown[]): { from: string; to: string; count: number }[] => {
  const counter = new Map<string, { from: string; to: string; count: number }>()
  for (const ev of evs) {
    if (!ev || typeof ev !== 'object') continue
    const { from, to } = extractStatusFlow((ev as Record<string, unknown>).payload)
    if (!from || !to) continue
    const key = `${from}→${to}`
    const cur = counter.get(key) ?? { from, to, count: 0 }
    cur.count += 1
    counter.set(key, cur)
  }
  return [...counter.values()].sort((a, b) => b.count - a.count)
}
/** 黑板条目 → 中文辅助 */
const BB_KIND_ZH: Record<string, string> = {
  fact: '事实', claim: '主张', evidence: '证据', challenge: '质疑',
  conflict: '冲突', decision: '决策', artifact: '交付物', note: '备注',
}
const BB_STATUS_ZH: Record<string, string> = {
  proposed: '待确认', accepted: '已确认', rejected: '已否决',
  conflict: '待裁决', decided: '已裁决', superseded: '已作废',
}
const BB_SCOPE_ZH: Record<string, string> = {
  tenant: '租户', project: '项目', task: '任务', run: '运行',
  workflow: '工作流', agent: '智能体',
}
const bbKind = (v: unknown) => BB_KIND_ZH[String(v ?? '').toLowerCase()] ?? valueLabel(v)
const bbStatus = (v: unknown) => BB_STATUS_ZH[String(v ?? '').toLowerCase()] ?? valueLabel(v)
const bbScope = (v: unknown) => BB_SCOPE_ZH[String(v ?? '').toLowerCase()] ?? valueLabel(v)
/** 黑板 content 常用字段的中文名 */
const BB_FIELD_ZH: Record<string, string> = {
  fact: '事实', title: '标题', summary: '摘要', note: '备注', text: '内容',
  outcome: '结论', rationale: '依据', reason: '原因', action: '动作',
  approved: '审批', approver_id: '审批人', submitted_decision: '提交决策',
  approval_result: '审批结果', approval_request: '审批请求',
  final_result: '最终结果', resolved_synthesis: '已裁决结论', synthesis: '综合结论',
  reconciled_synthesis: '对账结论', conflict: '冲突', conflict_key: '冲突类型',
  claim: '主张', contribution: '贡献', expert_id: '专家', author_id: '作者',
  source_lineage: '来源链路', evidence_refs: '证据引用', source_refs: '来源引用',
  payload: '载荷', role: '角色', decision: '决策', publish: '发布',
}
/** 常见半结构化取值的可读化 */
const BB_VALUE_ZH: Record<string, string> = {
  original: '原始版本', revised: '修订版本', publish: '发布',
  'reconciled synthesis': '对账后的综合结论',
  'initial synthesis': '初版综合结论',
  'accept reconciled claim': '采纳对账后的主张',
  'evidence reviewed': '证据已复核',
  'claims need reconciliation': '主张需要对齐',
  'claim-consistency': '主张一致性',
  'verified claim from critic': '评审提出的可验证主张',
  'verified claim from researcher': '研究提出的可验证主张',
  'integration-reviewer': '集成评审', 'source:researcher': '研究', 'source:critic': '评审',
  reviewer: '评审', critic: '评审', researcher: '研究', writer: '撰写',
}
type BBLeaf = { key: string; value: string }
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const bbReadable = (value: unknown): string => {
  const raw = String(value).trim()
  return BB_VALUE_ZH[raw] ?? BB_VALUE_ZH[raw.toLowerCase()] ?? raw
}
/** 把任意 JSON 拍平成「字段 → 可读值」，过滤 UUID 与内部 ID */
const bbLeaves = (value: unknown, parentKey = '', out: BBLeaf[] = [], depth = 0): BBLeaf[] => {
  if (depth > 3 || value === null || value === undefined) return out
  if (Array.isArray(value)) {
    if (parentKey === 'source_lineage') {
      if (value.length) out.push({ key: parentKey, value: `${value.length} 条` })
      return out
    }
    if (parentKey === 'evidence_refs' || parentKey === 'source_refs') {
      const refs = value
        .map(item => (typeof item === 'string' ? bbReadable(item) : ''))
        .filter(item => item && !UUID_RE.test(item))
      if (refs.length) out.push({ key: parentKey, value: refs.join('、') })
      return out
    }
    value.slice(0, 3).forEach(item => bbLeaves(item, parentKey, out, depth + 1))
    return out
  }
  if (typeof value === 'object') {
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) bbLeaves(v, k, out, depth + 1)
    return out
  }
  if (typeof value === 'boolean') {
    out.push({ key: parentKey, value: parentKey === 'approved' ? (value ? '通过' : '未通过') : (value ? '是' : '否') })
    return out
  }
  const str = String(value).trim()
  if (!str || parentKey.endsWith('_id') || UUID_RE.test(str) || /^[0-9a-f]{24,}$/i.test(str)) return out
  out.push({ key: parentKey, value: bbReadable(str) })
  return out
}
/** 不同类型条目优先展示的字段 */
const BB_TITLE_KEYS: Record<string, string[]> = {
  fact: ['fact', 'summary'],
  claim: ['claim', 'summary'],
  evidence: ['evidence', 'summary'],
  challenge: ['reason', 'challenge'],
  conflict: ['reason', 'conflict_key'],
  decision: ['outcome', 'approved', 'rationale', 'summary'],
  artifact: ['summary', 'reconciled_synthesis', 'synthesis', 'final_result'],
  note: ['action', 'summary', 'note', 'text'],
}
/** 黑板条目 → 可读标题 + 补充信息（不再展示裸 JSON / UUID） */
const BB_IDENTITY_KEYS = new Set(['author_id', 'role', 'expert_id', 'approver_id', 'entry_id'])
const bbView = (
  kind: unknown,
  content: unknown,
  fallback: string,
): { title: string; detail: string } => {
  if (!content || typeof content !== 'object') return { title: fallback, detail: '' }
  const obj = content as Record<string, unknown>
  const explicit = typeof obj.title === 'string' ? obj.title.trim() : ''
  const leaves = bbLeaves(content)
  const k = String(kind ?? '').toLowerCase()
  const preferred = BB_TITLE_KEYS[k] ?? []
  const preferredLeaf = preferred.map(key => leaves.find(leaf => leaf.key === key)).find(Boolean)
  const meaningfulLeaf = leaves.find(leaf => !BB_IDENTITY_KEYS.has(leaf.key))
  const primary = explicit ? undefined : (preferredLeaf ?? meaningfulLeaf)
  // 结构化字段（如 resolved_synthesis）没有可读值时，用字段名做标题
  const objectLabel = Object.keys(obj)
    .map(key => BB_FIELD_ZH[key])
    .find(Boolean)
  let title = explicit || (primary ? primary.value : '') || objectLabel || fallback
  if (primary?.key === 'approved') title = `审批${primary.value}`
  const detailItems: string[] = []
  const seenValues = new Set<string>(primary ? [primary.value, title] : [title])
  for (const leaf of leaves) {
    if (leaf === primary || seenValues.has(leaf.value)) continue
    seenValues.add(leaf.value)
    const label = BB_FIELD_ZH[leaf.key]
    detailItems.push(label ? `${label}：${leaf.value}` : leaf.value)
    if (detailItems.length === 3) break
  }
  return { title, detail: detailItems.join(' · ') }
}
/** 版本号去掉 v 前缀（如果已有） */
const bbAuthor = (authorId: unknown): string => {
  const id = text(authorId)
  if (!id || id === '暂无摘要') return '系统'
  // 去掉 machine-id 风格的下划线和短横线，保留可读前缀
  const parts = id.split(/[-_]/)
  const first = parts[0] || id
  const ABBREV: Record<string, string> = {
    'expert': '专家', 'integration': '集成', 'approval': '审批',
    'workflow': '工作流', 'resolution': '裁决', 'reviewer': '评审',
  }
  return ABBREV[first] ?? id
}

/** 任务看板：状态分栏 */
const KANBAN_COLUMNS: { key: string; label: string; statuses: string[] }[] = [
  { key: 'todo', label: '待处理', statuses: ['queued', 'pending', 'paused', 'waiting_approval', 'waiting_human', 'blocked'] },
  { key: 'doing', label: '进行中', statuses: ['running', 'retrying', 'cancelling'] },
  { key: 'done', label: '已完成', statuses: ['succeeded', 'completed', 'done'] },
  { key: 'failed', label: '异常', statuses: ['failed', 'timed_out', 'cancelled', 'canceled', 'interrupted'] },
]
/** 任务标题：优先取 input 中的可读字段 */
const taskTitle = (task: RecordValue): string => {
  const input = task.input
  if (typeof input === 'string' && input.trim()) return input.trim().slice(0, 80)
  if (input && typeof input === 'object') {
    const record = input as RecordValue
    for (const key of ['title', 'message', 'prompt', 'task', 'description', 'content']) {
      const value = record[key]
      if (typeof value === 'string' && value.trim()) return value.trim().slice(0, 80)
    }
  }
  return `任务 ${text(task.task_id, '').slice(-8) || '未命名'}`
}
/** 谁分的：created_by 可能是邮箱、用户 ID 或本机账号 */
const taskAssigner = (task: RecordValue, ownerId = '', ownerName = ''): string => {
  const creator = text(task.created_by, '')
  if (!creator) return '系统'
  if (ownerId && creator === ownerId) return ownerName || '我'
  if (creator.includes('@')) return creator.split('@')[0]
  if (UUID_RE.test(creator)) return '平台用户'
  return bbAuthor(creator)
}


/** 运行/事件状态中文名 */
const RUN_STATUS_ZH: Record<string, string> = {
  queued: '排队中', pending: '待执行', running: '执行中', in_progress: '执行中',
  interrupted: '已中断', paused: '已暂停', retrying: '重试中', timed_out: '已超时',
  succeeded: '已完成', completed: '已完成', failed: '失败', cancelled: '已取消', canceled: '已取消',
  // 事件 payload 里可能直接是中文，统一收口，避免中英混排
  排队中: '排队中', 运行中: '执行中', 执行中: '执行中', 已中断: '已中断',
  已完成: '已完成', 失败: '失败', 已取消: '已取消', 已超时: '已超时',
}
const runStatusLabel = (value: unknown) =>
  RUN_STATUS_ZH[String(value ?? '').trim()] ?? runStatusLabelByKey(value)
const runStatusLabelByKey = (value: unknown) =>
  RUN_STATUS_ZH[String(value ?? '').toLowerCase()] ?? valueLabel(value)

/** 事件类型中文名（未覆盖的类型回退到通用标签） */
const EVENT_TYPE_ZH: Record<string, string> = {
  'run.status_changed': '运行状态变更',
  'run.created': '运行已创建',
  'run.started': '开始执行',
  'run.completed': '执行完成',
  'run.failed': '执行失败',
  'run.cancelled': '已取消',
  'run.interrupted': '已中断',
  'run.resumed': '恢复执行',
  'task.created': '任务已创建',
  'node.started': '节点开始',
  'node.finished': '节点结束',
  'tool.called': '工具调用',
  'model.called': '模型调用',
}
const eventTypeZh = (value: unknown) => {
  const key = String(value ?? '').trim()
  if (!key) return '运行事件'
  return EVENT_TYPE_ZH[key] ?? EVENT_TYPE_ZH[key.replace(/\./g, '_')] ?? eventLabel(key)
}
/** 仅保留时分秒，并把 UTC 转成本地时间 */
const clock = (value: unknown): string => {
  if (typeof value !== 'string' || !value) return ''
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value.slice(11, 19)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(parsed.getHours())}:${pad(parsed.getMinutes())}:${pad(parsed.getSeconds())}`
}
/** 从运行输入里提取一句任务标题，替代「只有日期」的选项 */
const RUN_TITLE_KEYS = ['prompt', 'task', 'title', 'query', 'message', 'instruction', 'content', 'summary']
const runTitle = (value: unknown): string => {
  if (typeof value === 'string') return value.trim()
  if (value && typeof value === 'object') {
    const record = value as RecordValue
    for (const key of RUN_TITLE_KEYS) {
      const item = record[key]
      if (typeof item === 'string' && item.trim()) return item.trim()
    }
  }
  return ''
}
const clipTitle = (value: string, max = 36): string =>
  value.length > max ? `${value.slice(0, max)}…` : value
/** 毫秒 → 可读耗时 */
const fmtDuration = (ms: number): string => {
  if (!Number.isFinite(ms) || ms < 0) return '—'
  if (ms < 1000) return `${Math.round(ms)} 毫秒`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)} 秒`
  return `${Math.floor(ms / 60000)} 分 ${Math.round((ms % 60000) / 1000)} 秒`
}
/** 事件 payload → 可读描述（过滤状态字段，避免和时间线重复） */
const eventDetail = (payload: unknown): string => {
  if (payload === null || payload === undefined) return ''
  if (typeof payload === 'string') return payload.trim()
  if (typeof payload === 'object') {
    const record = payload as RecordValue
    for (const key of ['message', 'summary', 'detail', 'reason', 'error', 'text', 'content', 'output']) {
      const value = record[key]
      if (typeof value === 'string' && value.trim()) return value.trim()
    }
    const allLeaves = bbLeaves(payload)
    const leaves = allLeaves.filter(
      leaf => !['from_status', 'to_status', 'from_state', 'to_state', 'from', 'to', 'version'].includes(leaf.key),
    )
    if (leaves.length) {
      return leaves
        .slice(0, 3)
        .map(leaf => (BB_FIELD_ZH[leaf.key] ? `${BB_FIELD_ZH[leaf.key]}：${leaf.value}` : leaf.value))
        .join(' · ')
    }
    // 全是状态字段时由状态流转展示，不再重复输出 JSON
    if (allLeaves.length) return ''
  }
  return compact(payload)
}

/** 小徽章组件（状态色） */
const Badge: React.FC<{ status?: string; children: React.ReactNode }> = ({ status, children }) => (
  <span className={`ctl-badge ${statusClass(status)}`}>{children}</span>
)

export function AgentCollaborationCenter() {
  const queryClient = useQueryClient()
  const { session } = useAuth()
  const ownerId = session?.user?.user_id ?? ''
  const ownerName = session?.user?.display_name || session?.user?.email?.split('@')[0] || ''
  const [scope, setScope] = useState('')
  const [scopeId, setScopeId] = useState('')
  const [runId, setRunId] = useState('')
  const [selectedEntry, setSelectedEntry] = useState('')
  const [kind, setKind] = useState('')
  const [tab, setTab] = useState<'team' | 'runtime' | 'blackboard'>('team')
  const [bbPage, setBbPage] = useState(0)
  const [bbMode, setBbMode] = useState<'entries' | 'kanban'>('kanban')
  const [evPage, setEvPage] = useState(0)
  const BB_PAGE_SIZE = 20
  const EV_PAGE_SIZE = 10
  // 筛选条件变化时重置到第一页
  useEffect(() => { setBbPage(0) }, [scope, scopeId, kind])
  // 切换运行时重置事件分页
  useEffect(() => { setEvPage(0) }, [runId])
  const [liveRefresh, setLiveRefresh] = useState(false)
  const [liveRunEvents, setLiveRunEvents] = useState<RecordValue[]>([])
  const [runStreamStatus, setRunStreamStatus] = useState<'idle' | 'connecting' | 'connected' | 'reconnecting' | 'closed' | 'unsupported'>('idle')
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
  // 看板数据：工作台任务（谁分的、谁领的、完成情况）
  const tasksQuery = useQuery({
    queryKey: ['collaboration', 'workbench-tasks'],
    queryFn: () => apiClient.get<unknown>('/platform/v1/workbench/tasks?limit=100'),
    refetchInterval: 20000,
  })
  const tasks = useMemo(() => unwrap<RecordValue>(tasksQuery.data), [tasksQuery.data])
  const agentNames = useMemo(() => {
    const map = new Map<string, string>()
    for (const agent of agents) {
      const definition = (agent.definition && typeof agent.definition === 'object' ? agent.definition : {}) as RecordValue
      const id = text(agent.item_id, '')
      const name = text(definition.name, '')
      if (id && name && name !== '—') map.set(id, name)
    }
    return map
  }, [agents])
  const agentLabel = (id: string) => (id ? agentNames.get(id) ?? id : '未分派')
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

  // Run events are the only source of node/edge telemetry; no graph snapshot is assumed.
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

  // Subscribe to the same durable Run broker used by workers. Reconnect by
  // opening a fresh EventSource so a rejected/expired Last-Event-ID cannot trap
  // the browser in an endless retry loop. Replayed history is deduplicated below.
  useEffect(() => {
    setLiveRunEvents([])
    if (!runId.trim() || !runQuery.isSuccess) {
      setRunStreamStatus('idle')
      return
    }
    if (typeof EventSource === 'undefined') {
      setRunStreamStatus('unsupported')
      return
    }
    let disposed = false
    let retryTimer: number | undefined
    let source: EventSource | undefined
    const eventNames = ['step_progress', 'token_stream', 'tool_output', 'run_complete', 'run_error', 'error']
    const connect = () => {
      if (disposed) return
      setRunStreamStatus('connecting')
      source = new EventSource(`/api/platform/v1/runs/${encodeURIComponent(runId.trim())}/stream`)
      const currentSource = source
      currentSource.onopen = () => setRunStreamStatus('connected')
      currentSource.onerror = () => {
        setRunStreamStatus('reconnecting')
        currentSource.close()
        if (!disposed) retryTimer = window.setTimeout(connect, 2000)
      }
      const handleRunEvent = (rawEvent: Event) => {
        const message = rawEvent as MessageEvent<string>
        let payload: unknown
        try {
          payload = JSON.parse(message.data)
        } catch {
          return
        }
        const raw = payload && typeof payload === 'object' ? payload as RecordValue : {}
        if (raw.event_type === 'RECONNECT_REQUIRED') {
          setRunStreamStatus('reconnecting')
          void eventsQuery.refetch()
          currentSource.close()
          if (!disposed) retryTimer = window.setTimeout(connect, 250)
          return
        }
        const normalized = normalizeRunSseEvent(payload, message.lastEventId)
        if (!normalized) return
        setLiveRunEvents(current => mergeRunRuntimeEvents(current, [normalized]))
        if (raw.event_type === 'COMPLETE' || raw.event_type === 'ERROR') {
          setRunStreamStatus('closed')
          currentSource.close()
          void runQuery.refetch()
          void eventsQuery.refetch()
        }
      }
      for (const name of eventNames) currentSource.addEventListener(name, handleRunEvent)
    }
    connect()
    return () => {
      disposed = true
      if (retryTimer !== undefined) window.clearTimeout(retryTimer)
      source?.close()
    }
  }, [runId, runQuery.isSuccess, runQuery.refetch, eventsQuery.refetch])

  // The current Entry contract exposes source_refs; the UI does not invent locator fields.
  const entriesQuery = useQuery({
    queryKey: ['collaboration', 'blackboard', scope, scopeId, kind, bbPage],
    enabled: true,
    queryFn: () => {
      const hasScopeFilter = Boolean(scopeId.trim())
      const params = new URLSearchParams({ limit: String(BB_PAGE_SIZE), offset: String(bbPage * BB_PAGE_SIZE) })
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
    refetchInterval: liveRefresh && Boolean(scopeId.trim()) && liveStatus === 'connected' ? false : 20000,
  })
  const entries = useMemo(() => unwrap<RecordValue>(entriesQuery.data), [entriesQuery.data])
  const hasVersion = useMemo(() => entries.some(entry => entryVersion(entry) > 0), [entries])
  const selected = useMemo(
    () => entries.find(entry => text(entry.entry_id, '') === selectedEntry),
    [entries, selectedEntry],
  )
  const selectedView = useMemo(
    () => (selected ? bbView(selected.kind, selected.content, '未命名条目') : { title: '', detail: '' }),
    [selected],
  )
  // Esc 关闭详情弹窗
  useEffect(() => {
    if (!selectedEntry) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setSelectedEntry('')
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selectedEntry])
  const historyQuery = useQuery({
    queryKey: ['collaboration', 'blackboard-history', selectedEntry],
    enabled: Boolean(selectedEntry),
    queryFn: () => apiClient.get<unknown>(`/platform/v1/blackboard/entries/${encodeURIComponent(selectedEntry)}/history`),
  })
  const history = unwrap<RecordValue>(historyQuery.data)
  const events = useMemo(() => {
    const items = unwrap<RecordValue>(eventsQuery.data)
    return mergeRunRuntimeEvents(items, liveRunEvents)
  }, [eventsQuery.data, liveRunEvents])

  // 运行快照 / 事件分页派生值
  const runSnapshot = runQuery.data
  const selectedRunTitle = runTitle(runSnapshot?.input)
  const runDurationMs = (() => {
    if (!runSnapshot) return NaN
    if (typeof runSnapshot.duration_ms === 'number') return runSnapshot.duration_ms
    const start = Date.parse(String(runSnapshot.created_at ?? ''))
    const end = Date.parse(String(runSnapshot.updated_at ?? ''))
    return Number.isFinite(start) && Number.isFinite(end) ? end - start : NaN
  })()
  const runOutput = (() => {
    const output = runSnapshot?.output
    if (typeof output === 'string') return output
    if (output && typeof output === 'object') {
      const record = output as RecordValue
      for (const key of ['summary', 'output', 'result', 'message', 'text']) {
        const value = record[key]
        if (typeof value === 'string' && value.trim()) return value.trim()
      }
    }
    return ''
  })()
  const evTotalPages = Math.max(1, Math.ceil(events.length / EV_PAGE_SIZE))
  const evCurrentPage = Math.min(evPage, evTotalPages - 1)
  const evSlice = events.slice(evCurrentPage * EV_PAGE_SIZE, (evCurrentPage + 1) * EV_PAGE_SIZE)

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
          `${date(payload.occurred_at)} · ${eventTypeZh(text(payload.event_type, ''))} · v${String(payload.entry_version ?? '—')} · ${text(payload.entry_id, '条目未提供').slice(0, 12)}`,
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
        <p className="eyebrow">运营 · 智能体</p>
        <h1>智能体协作中心</h1>
        <p>查看智能体注册信息、真实运行事件与协作黑板不可变记录。</p>
      </div>

      <nav className="panel-tabs" role="tablist" aria-label="智能体协作分区">
        {([
          ['team', '智能体团队', agents.length],
          ['runtime', '运行执行时间线', events.length],
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
          <div><p className="eyebrow">团队目录</p><h2 id="collab-team-title">智能体团队</h2></div>
          <span className="muted">注册表 · 30 秒刷新</span>
        </div>
        {agentsQuery.isPending ? <LoadingState label="正在读取智能体注册表" /> :
          agentsQuery.isError ? <ErrorState title="智能体列表读取失败" description={errorText(agentsQuery.error)} onRetry={() => void agentsQuery.refetch()} /> :
          agents.length === 0 ? <EmptyState title="暂无已注册智能体" description="注册表未返回智能体定义，不会用示例卡片代替真实团队成员。" /> :
          <AgentTeamGraph agents={agents} />}
      </section>
      )}

      {tab === 'runtime' && (
      <section className="collab-section" aria-labelledby="collab-run-title">
        <div className="collab-section-heading"><div><p className="eyebrow">运行可观测</p><h2 id="collab-run-title">运行执行时间线</h2></div><span className="muted">事件流：{{ idle: '未连接', connecting: '连接中', connected: '实时连接', reconnecting: '断线恢复中', closed: '运行已结束', unsupported: '浏览器不支持实时流' }[runStreamStatus]} · 10 秒快照补偿</span></div>
        <div className="collab-controls"><label>最近运行
          <select value={recentRuns.some(r => text(r.run_id, '') === runId) ? runId : ''} onChange={event => setRunId(event.target.value)}>
            <option value="">— 选择一个运行 —</option>
            {recentRuns.map((r, idx) => {
              const id = text(r.run_id, '')
              if (!id) return null
              return <option key={id} value={id}>{text(r.title, '') ? `${clipTitle(text(r.title, ''))} · ` : ''}{runStatusLabel(r.status)} · {date(r.updated_at ?? r.created_at)} ({idx + 1}/{recentRuns.length})</option>
            })}
          </select>
        </label><details className="collab-manual-run"><summary>手动输入运行标识</summary><label>运行标识<input value={runId} onChange={event => setRunId(event.target.value)} placeholder="粘贴运行标识，或从上方下拉选择" /></label></details>{runsQuery.isPending && <span className="muted">正在拉取最近运行…</span>}{runsQuery.isError && <span className="muted">最近运行列表获取失败：{errorText(runsQuery.error)}</span>}</div>
        {!runId.trim() ? <EmptyState title="选择一个运行" description={recentRuns.length > 0 ? '从下拉框选择一个最新运行，或手动输入运行标识。' : '暂无最近运行，可手动输入运行标识，或先触发一次运行。'} /> :
          runQuery.isPending ? <LoadingState label="正在读取运行快照" /> :
          runQuery.isError ? <ErrorState title="运行快照读取失败" description={errorText(runQuery.error)} onRetry={() => void runQuery.refetch()} /> :
          <div className="collab-run-layout">
            {/* ==== 运行概览卡 ==== */}
            <article className="collab-run-overview">
              <div className="rov-head">
                <div>
                  <div className="rov-title" title={selectedRunTitle || undefined}>{selectedRunTitle ? clipTitle(selectedRunTitle, 48) : '运行记录'}</div>
                  <div className="rov-sub">创建于 {date(runQuery.data.created_at)} · 更新于 {date(runQuery.data.updated_at)}</div>
                </div>
                <Badge status={text(runQuery.data.status, '')}>{runStatusLabel(runQuery.data.status)}</Badge>
              </div>
              <div className="rov-stats">
                <div><span>总耗时</span><strong>{fmtDuration(runDurationMs)}</strong></div>
                <div><span>事件数</span><strong>{events.length} 条</strong></div>
                <div><span>智能体</span><strong>{agentLabel(text(runQuery.data.agent_id, ''))}</strong></div>
                <div><span>版本</span><strong>{runQuery.data.version != null ? `v${String(runQuery.data.version)}` : '—'}</strong></div>
              </div>
              <dl>
                <div><dt>工作流</dt><dd>{text(runQuery.data.workflow_id, '未绑定工作流')}</dd></div>
                <div><dt>工作流版本</dt><dd>{runQuery.data.workflow_version != null ? `v${String(runQuery.data.workflow_version)}` : '—'}</dd></div>
                <div className="rov-output-row"><dt>输出摘要</dt><dd className="rov-output">{runOutput ? compact(runOutput) : '暂无输出'}</dd></div>
              </dl>
              {runQuery.data.last_error ? (
                <p className="rov-error">⚠ {compact(runQuery.data.last_error)}</p>
              ) : null}
            </article>

            {/* ==== 可观测区域 ==== */}
            <WorkflowRunCanvas events={events} />

            <div className="collab-run-observability">
              {/* 事件摘要 + 时间线 */}
              <div className="collab-run-canvas">
                <h3>状态流转摘要</h3>
                <p>从 {events.length} 条事件里提取的状态变化路径</p>
                <div className="collab-run-summary-stats">
                  <span className="collab-stat-pill">事件数 <strong>{events.length}</strong></span>
                  {(() => {
                    const flows = summarizeStatusFlows(events)
                    return flows.slice(0, 3).map((f, i) => (
                      <span key={i} className="collab-stat-pill">
                        <span className={`ctl-badge ${statusClass(f.from)}`}>{runStatusLabel(f.from)}</span>
                        →
                        <span className={`ctl-badge ${statusClass(f.to)}`}>{runStatusLabel(f.to)}</span>
                        ×{f.count}
                      </span>
                    ))
                  })()}
                </div>
              </div>

              <div className="collab-event-panel">
                <h3>执行事件时间线</h3>
                {eventsQuery.isPending ? <LoadingState label="正在读取运行事件" /> :
                  eventsQuery.isError ? <ErrorState title="运行事件读取失败" description={errorText(eventsQuery.error)} onRetry={() => void eventsQuery.refetch()} /> :
                  events.length === 0 ? <EmptyState title="暂无事件记录" description="事件接口没有返回节点级遥测。" /> :
                  <>
                    <ol className="collab-timeline" start={evCurrentPage * EV_PAGE_SIZE + 1}>{evSlice.map((event, index) => {
                      const globalIndex = evCurrentPage * EV_PAGE_SIZE + index
                      const etype = text(event.event_type ?? event.type, '运行事件')
                      const occ = text(event.occurred_at, '')
                      const payload = event.payload ?? event.summary ?? event.data
                      const { from, to } = extractStatusFlow(payload)
                      const detail = eventDetail(payload)
                      const dotClass = to ? statusClass(to) : statusClass(event.status)
                      const prevTime = globalIndex > 0 ? Date.parse(String(events[globalIndex - 1]?.occurred_at ?? '')) : NaN
                      const thisTime = Date.parse(occ)
                      const deltaMs = Number.isFinite(prevTime) && Number.isFinite(thisTime) ? thisTime - prevTime : NaN
                      return (
                        <li key={text(event.event_id, `${String(event.sequence ?? 'event')}-${globalIndex}`)}>
                          <span className={`ctl-dot ${dotClass}`} />
                          <div className="ctl-card">
                            <div className="ctl-meta">
                              <span className="ctl-type">{eventTypeZh(etype)}</span>
                              {occ && <time className="ctl-time">{clock(occ)}</time>}
                              {Number.isFinite(deltaMs) && deltaMs >= 0 && <span className="ctl-delta">+{fmtDuration(deltaMs)}</span>}
                            </div>
                            <div className="ctl-body">
                              {from && to ? (
                                <span className="ctl-status-flow">
                                  <Badge status={from}>{runStatusLabel(from)}</Badge>
                                  <span className="ctl-arrow">→</span>
                                  <Badge status={to}>{runStatusLabel(to)}</Badge>
                                </span>
                              ) : (
                                <p>{detail || '无附加信息'}</p>
                              )}
                            </div>
                            {from && to && detail ? <p className="ctl-foot">{detail}</p> : null}
                            <div className="ctl-foot">
                              {event.sequence != null && <>#{String(event.sequence)} </>}
                              {text(event.node_id, '') && <>· 节点 {text(event.node_id, '')} </>}
                              {text(event.agent_id, '') && <>· 智能体 {agentLabel(text(event.agent_id, ''))}</>}
                            </div>
                          </div>
                        </li>
                      )
                    })}</ol>
                    {events.length > EV_PAGE_SIZE && <div className="collab-bb-pagination">
                      <button type="button" disabled={evCurrentPage === 0} onClick={() => setEvPage(page => Math.max(0, page - 1))}>← 上一页</button>
                      <span>第 {evCurrentPage + 1} / {evTotalPages} 页 · 共 {events.length} 条</span>
                      <button type="button" disabled={evCurrentPage + 1 >= evTotalPages} onClick={() => setEvPage(page => page + 1)}>下一页 →</button>
                    </div>}
                  </>}
                <p className="muted">节点/边状态仅在服务端事件明确提供时呈现；当前事件列表不等同于完整的工作流执行图。</p>
              </div>
            </div>
          </div>}
      </section>
      )}

      {tab === 'blackboard' && (
      <section className="collab-section" aria-labelledby="collab-blackboard-title">
        <div className="collab-section-heading">
          <div><p className="eyebrow">协作黑板</p><h2 id="collab-blackboard-title">协作黑板</h2></div>
          <div className="collab-bb-views" role="tablist" aria-label="黑板视图切换">
            <button type="button" role="tab" aria-selected={bbMode === 'kanban'} className={bbMode === 'kanban' ? 'active' : ''} onClick={() => setBbMode('kanban')}>任务看板</button>
            <button type="button" role="tab" aria-selected={bbMode === 'entries'} className={bbMode === 'entries' ? 'active' : ''} onClick={() => setBbMode('entries')}>条目时间线</button>
          </div>
        </div>
        {bbMode === 'entries' && (<>
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
          <label>范围标识<input value={scopeId} onChange={event => { setScopeId(event.target.value); setSelectedEntry('') }} placeholder="留空 = 展示全部条目" /></label>
          <label>条目类型<select value={kind} onChange={event => setKind(event.target.value)}><option value="">全部类型</option>{['fact','claim','evidence','challenge','conflict','decision','artifact','note'].map(value => <option key={value} value={value}>{bbKind(value)}</option>)}</select></label>
          <label className="collab-bb-toggle"><input type="checkbox" checked={liveRefresh} onChange={event => setLiveRefresh(event.target.checked)} disabled={!scopeId.trim()} />实时刷新</label>
        </div>
        {liveRefresh && (
          <div className={liveStatus === 'error' ? 'collab-bb-live error' : 'collab-bb-live'}>
            <strong>{{ idle: '待启用', connecting: '连接中…', connected: '已连接', error: '连接中断，将自动重连' }[liveStatus]}</strong>
            {lastLiveEvent && <span>最近事件：{lastLiveEvent}</span>}
            <span>连接正常时暂停轮询；断线恢复期间自动回到 20 秒快照刷新。</span>
          </div>
        )}
        {entriesQuery.isPending ? <LoadingState label="正在读取黑板条目" /> :
          entriesQuery.isError ? <ErrorState title="协作黑板读取失败" description={errorText(entriesQuery.error)} onRetry={() => void entriesQuery.refetch()} /> :
          entries.length === 0 ? <EmptyState title="暂无黑板记录" description={scopeId.trim() ? `范围 ${scope}:${scopeId} 下无条目。` : '当前租户下尚未写入任何黑板条目。'} /> :
          <div className="collab-blackboard-layout">
            <div className="collab-entry-table">
              <div className="collab-entry-thead">
                <span className="col-kind">类型</span>
                <span className="col-title">标题 / 内容</span>
                <span className="col-status">状态</span>
                <span className="col-scope">范围</span>
                <span className="col-author">作者</span>
                <span className="col-time">时间</span>
                {hasVersion && <span className="col-ver">版本</span>}
              </div>
              <ol className="collab-entry-list">{entries.map((entry, index) => {
                const kindVal = bbKind(entry.kind)
                const statusVal = bbStatus(entry.status)
                const statusKey = String(entry.status ?? '').toLowerCase()
                const view = bbView(entry.kind, entry.content, `未命名${kindVal}`)
                const authorVal = bbAuthor(entry.author_id)
                const versionVal = entryVersion(entry)
                const selected = selectedEntry === entry.entry_id
                return (
                <li key={text(entry.entry_id, String(index))}
                    className={selected ? 'collab-row selected' : 'collab-row'}
                    onClick={() => {
                      setSelectedEntry(text(entry.entry_id, ''))
                      setEditContent(JSON.stringify(entry.content ?? {}, null, 2))
                      setEditError('')
                      reviseMutation.reset()
                      challengeMutation.reset()
                      decisionMutation.reset()
                    }}>
                  <span className="col-kind">{kindVal}</span>
                  <span className="col-title">
                    <strong title={view.title}>{view.title}</strong>
                    {view.detail && <small title={view.detail}>{view.detail}</small>}
                  </span>
                  <span className="col-status"><span className={`bb-status status-${statusKey}`}>{statusVal}</span></span>
                  <span className="col-scope">
                    {entry.scope ? bbScope(entry.scope) : '—'}
                  </span>
                  <span className="col-author" title={authorVal}>{authorVal}</span>
                  <span className="col-time">{date(entry.created_at)}</span>
                  {hasVersion && <span className="col-ver">{versionVal ? `v${versionVal}` : '—'}</span>}
                </li>)
              })}</ol>
              <div className="collab-bb-pagination">
                <button disabled={bbPage === 0} onClick={() => setBbPage(p => Math.max(0, p - 1))}>← 上一页</button>
                <span>第 {bbPage + 1} 页（每页 {BB_PAGE_SIZE} 条）</span>
                <button disabled={entries.length < BB_PAGE_SIZE} onClick={() => setBbPage(p => p + 1)}>下一页 →</button>
              </div>
            </div>
            {selectedEntry && selected && (
              <div className="collab-modal-mask" onClick={() => setSelectedEntry('')}>
                <div
                  className="collab-modal"
                  role="dialog"
                  aria-modal="true"
                  aria-label="黑板条目详情"
                  onClick={event => event.stopPropagation()}
                >
                  <div className="collab-modal-head">
                    <div className="collab-modal-title">
                      <p className="eyebrow">黑板条目 · {bbKind(selected.kind)}</p>
                      <h3>{selectedView.title}</h3>
                    </div>
                    <button type="button" className="collab-modal-close" onClick={() => setSelectedEntry('')} aria-label="关闭">✕</button>
                  </div>

                  <div className="collab-modal-body">
                    <div className="collab-bb-meta">
                      <span className={`bb-status status-${String(selected.status ?? '').toLowerCase()}`}>{bbStatus(selected.status)}</span>
                      <span>当前版本 v{entryVersion(selected) || '—'}</span>
                      {selected.scope ? <span>范围 {bbScope(selected.scope)}</span> : null}
                      <span>作者 {bbAuthor(selected.author_id)}</span>
                      <span>{date(selected.created_at)}</span>
                    </div>
                    {selectedView.detail && <p className="collab-bb-summary">{selectedView.detail}</p>}

                    <section className="collab-modal-section">
                      <h4>版本历史</h4>
                      {historyQuery.isPending ? <LoadingState label="正在读取版本历史" /> :
                        historyQuery.isError ? <ErrorState title="版本历史读取失败" description={blackboardError(historyQuery.error)} onRetry={() => void historyQuery.refetch()} /> :
                        history.length === 0 ? <EmptyState title="暂无历史版本" description="仓库未返回版本记录。" /> :
                        <ol className="collab-history-list">{history.map((version, index) => {
                          const historyView = bbView(selected?.kind ?? version.kind, version.content, '无内容摘要')
                          return (
                            <li key={text(version.version, String(index))}>
                              <strong>版本 v{entryVersion(version) || text(version.version)}</strong>
                              <span>{bbStatus(version.status)} · {date(version.created_at)}</span>
                              <p>{historyView.detail ? `${historyView.title} · ${historyView.detail}` : historyView.title}</p>
                              <small>来源引用：{Array.isArray(version.source_refs) && version.source_refs.length ? `${version.source_refs.length} 条` : '无'}</small>
                            </li>
                          )
                        })}</ol>}
                    </section>

                    <div className="collab-bb-actions">
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
                        <label>证据引用（可选，逗号或换行分隔）<textarea rows={2} value={challengeRefs} onChange={event => setChallengeRefs(event.target.value)} placeholder="填写来源编号，可留空" /></label>
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
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>}
        </>)}
        {bbMode === 'kanban' && (
          tasksQuery.isPending ? <LoadingState label="正在读取任务看板" /> :
          tasksQuery.isError ? <ErrorState title="任务看板读取失败" description={errorText(tasksQuery.error)} onRetry={() => void tasksQuery.refetch()} /> :
          <div className="collab-kanban">
            {KANBAN_COLUMNS.map(column => {
              const columnTasks = tasks.filter(task => column.statuses.includes(String(task.status ?? '').toLowerCase()))
              return (
                <section key={column.key} className={`collab-kanban-col kanban-${column.key}`}>
                  <header><strong>{column.label}</strong><span>{columnTasks.length}</span></header>
                  <div className="collab-kanban-list">
                    {columnTasks.length === 0 ? <p className="collab-kanban-empty">暂无任务</p> : columnTasks.map((task, index) => {
                      const taskId = text(task.task_id, '')
                      const status = String(task.status ?? '')
                      return (
                        <Link key={taskId || String(index)} to={taskId ? `/operations/tasks/${encodeURIComponent(taskId)}` : '/operations/tasks'} className="collab-kanban-card">
                          <div className="kk-head">
                            <Badge status={status}>{valueLabel(task.status)}</Badge>
                            <time>{date(task.updated_at ?? task.created_at)}</time>
                          </div>
                          <strong className="kk-title">{taskTitle(task)}</strong>
                          <div className="kk-meta">
                            <span>分派：{taskAssigner(task, ownerId, ownerName)}</span>
                            <span>接手：{agentLabel(text(task.agent_id, ''))}</span>
                          </div>
                          {status.toLowerCase() === 'failed' && task.last_error ? <p className="kk-error">{compact(task.last_error)}</p> : null}
                        </Link>
                      )
                    })}
                  </div>
                </section>
              )
            })}
          </div>
        )}
        {bbMode === 'kanban' && <p className="muted collab-kanban-hint">点击任务卡片进入任务详情；数据每 20 秒刷新。</p>}
      </section>
      )}
    </section>
  )
}
