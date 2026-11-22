import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Activity, CheckCircle2, Clock3, ListTodo, Wallet } from 'lucide-react'

import { apiClient } from '@/lib/api'
import { unwrapList } from '@/features/tasks/task-model'
import type { TaskRecord } from '@/features/tasks/task-model'

type AnyRecord = Record<string, unknown>
export type HomeTrendPoint = {
  label: string
  today?: number
  completed?: number
  inProgress?: number
}

function asRecord(value: unknown): AnyRecord {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as AnyRecord)
    : {}
}

function firstNumber(source: AnyRecord, keys: string[]): number | undefined {
  for (const key of keys) {
    const value = source[key]
    if (typeof value === 'number' && Number.isFinite(value)) return value
    if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) {
      return Number(value)
    }
  }
  return undefined
}

function trendRows(payload: unknown): unknown[] {
  if (Array.isArray(payload)) return payload
  const root = asRecord(payload)
  const data = asRecord(root.data)
  for (const candidate of [
    root.items, root.points, root.trends, root.series,
    data.items, data.points, data.trends, data.series,
  ]) {
    if (Array.isArray(candidate)) return candidate
  }
  if (Array.isArray(root.data)) return root.data
  return []
}

function pointLabel(point: AnyRecord, index: number): string {
  const value = point.date ?? point.label ?? point.timestamp ?? point.bucket ?? point.created_at
  if (typeof value !== 'string' && typeof value !== 'number') return String(index + 1)
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? String(value)
    : new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric' }).format(date)
}

export function normalizeHomeTrendPoints(payload: unknown): HomeTrendPoint[] {
  return trendRows(payload).map((row, index) => {
    const point = asRecord(row)
    return {
      label: pointLabel(point, index),
      today: firstNumber(point, ['today', 'today_count', 'task_count', 'tasks', 'total', 'count']),
      completed: firstNumber(point, ['completed', 'completed_count', 'done', 'succeeded_count']),
      inProgress: firstNumber(point, ['in_progress', 'in_progress_count', 'running', 'active_count']),
    }
  })
}

function statusOf(task: TaskRecord): string {
  return String(task.status ?? '').toLowerCase()
}

function isCompleted(status: string): boolean {
  return ['completed', 'succeeded', 'success', 'done'].includes(status)
}

function isInProgress(status: string): boolean {
  return ['running', 'queued', 'pending', 'accepted', 'in_progress', 'dispatching'].includes(status)
}

function costOf(task: TaskRecord): number | undefined {
  const usage = asRecord(task.usage)
  const candidates = [
    task.total_cost, task.cost, task.cost_usd, task.estimated_cost,
    usage.total_cost, usage.cost, usage.cost_usd, usage.estimated_cost_usd,
  ]
  for (const value of candidates) {
    const parsed = typeof value === 'number' ? value :
      typeof value === 'string' && value.trim() ? Number(value) : NaN
    if (Number.isFinite(parsed)) return parsed
  }
  return undefined
}

function formatCount(value: number | undefined): string {
  return value == null ? '暂无数据' : new Intl.NumberFormat('zh-CN').format(value)
}

function formatRate(value: number | undefined): string {
  return value == null ? '暂无数据' : `${value.toFixed(1)}%`
}

function formatDuration(value: number | undefined): string {
  if (value == null) return '暂无数据'
  return value >= 1000 ? `${(value / 1000).toFixed(1)} 秒` : `${Math.round(value)} 毫秒`
}

function formatCost(value: number | undefined): string {
  return value == null ? '暂无数据' : `¥${value.toFixed(2)}`
}

function TrendChart({ points, loading, failed }: {
  points: HomeTrendPoint[]
  loading: boolean
  failed: boolean
}) {
  const width = 760
  const height = 250
  const left = 42
  const right = 18
  const top = 18
  const bottom = 38
  const plotHeight = height - top - bottom
  const values = points.flatMap((point) =>
    [point.today, point.completed, point.inProgress].filter((value): value is number => value != null),
  )
  const max = Math.max(1, ...values)
  const x = (index: number) => left + (points.length < 2 ? (width - left - right) / 2 : index / (points.length - 1) * (width - left - right))
  const y = (value: number) => top + plotHeight - value / max * plotHeight
  const path = (key: 'today' | 'completed' | 'inProgress') => {
    let started = false
    return points.map((point, index) => {
      const value = point[key]
      if (value == null) { started = false; return '' }
      const command = started ? 'L' : 'M'
      started = true
      return `${command} ${x(index)} ${y(value)}`
    }).filter(Boolean).join(' ')
  }
  const hasData = values.length > 0
  return (
    <section className="home-trend-panel" aria-labelledby="home-trend-title">
      <header className="home-panel-heading">
        <div>
          <h2 id="home-trend-title">任务趋势</h2>
          <p>今日任务、已完成与进行中任务变化</p>
        </div>
        <Link to="/workspace/tasks">任务中心 →</Link>
      </header>
      <div className="home-trend-legend" aria-label="图例">
        <span><i className="today" />今日</span>
        <span><i className="completed" />已完成</span>
        <span><i className="progress" />进行中</span>
      </div>
      {loading ? <div className="home-trend-state">正在加载趋势数据…</div> :
        failed ? <div className="home-trend-state" role="status">趋势数据暂不可用</div> :
        !hasData ? <div className="home-trend-state"><Activity size={24} /><strong>暂无数据</strong><span>接入任务趋势数据后将在此展示</span></div> :
        <svg className="home-trend-svg" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="今日、已完成、进行中任务折线图">
          {[0, 1, 2, 3].map((tick) => {
            const yy = top + plotHeight * tick / 3
            return <g key={tick}>
              <line x1={left} x2={width - right} y1={yy} y2={yy} className="home-trend-grid" />
              <text x={left - 10} y={yy + 4} textAnchor="end" className="home-trend-axis">{Math.round(max * (1 - tick / 3))}</text>
            </g>
          })}
          <path d={path('today')} className="home-trend-line today" />
          <path d={path('completed')} className="home-trend-line completed" />
          <path d={path('inProgress')} className="home-trend-line progress" />
          {points.map((point, index) => <text key={`${point.label}-${index}`} x={x(index)} y={height - 10} textAnchor="middle" className="home-trend-axis">{point.label}</text>)}
        </svg>
      }
    </section>
  )
}

export function Dashboard() {
  const tasksQuery = useQuery({
    queryKey: ['home-dashboard', 'tasks'],
    queryFn: () => apiClient.get<unknown>('/platform/v1/workbench/tasks'),
    refetchInterval: 30_000,
    staleTime: 15_000,
    retry: false,
  })
  const trendsQuery = useQuery({
    queryKey: ['home-dashboard', 'trends'],
    queryFn: () => apiClient.get<unknown>('/platform/v1/observability/trends'),
    refetchInterval: 30_000,
    staleTime: 15_000,
    retry: false,
  })
  const tasks = useMemo(() => unwrapList<TaskRecord>(tasksQuery.data), [tasksQuery.data])
  const completed = tasks.filter((task) => isCompleted(statusOf(task))).length
  const inProgress = tasks.filter((task) => isInProgress(statusOf(task))).length
  const successRate = tasks.length ? completed / tasks.length * 100 : tasksQuery.isSuccess ? 0 : undefined
  const durations = tasks.map((task) => task.duration_ms).filter((value): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0)
  const costs = tasks.map(costOf).filter((value): value is number => value != null)
  const averageDuration = durations.length ? durations.reduce((sum, value) => sum + value, 0) / durations.length : undefined
  const totalCost = costs.length ? costs.reduce((sum, value) => sum + value, 0) : undefined
  const points = useMemo(() => normalizeHomeTrendPoints(trendsQuery.data), [trendsQuery.data])
  const metrics = [
    { label: '任务数', value: formatCount(tasksQuery.isSuccess ? tasks.length : undefined), note: '任务总量', icon: <ListTodo size={18} />, tone: 'blue' },
    { label: '成功率', value: formatRate(successRate), note: tasks.length ? `${completed} / ${tasks.length} 已完成` : '暂无任务', icon: <CheckCircle2 size={18} />, tone: 'green' },
    { label: '平均耗时', value: formatDuration(averageDuration), note: durations.length ? `${durations.length} 条有效耗时` : '暂无数据', icon: <Clock3 size={18} />, tone: 'violet' },
    { label: '总成本', value: formatCost(totalCost), note: costs.length ? `${costs.length} 条成本记录` : '暂无数据', icon: <Wallet size={18} />, tone: 'orange' },
  ]
  return (
    <section className="page-content home-dashboard-r3">
      <header className="home-dashboard-heading">
        <div>
          <p className="eyebrow">AGENT PLATFORM / WORKSPACE</p>
          <h1>工作台</h1>
          <p>统一查看任务运行指标与执行趋势</p>
        </div>
        <div className="home-dashboard-actions">
          <Link to="/search" className="home-dashboard-search">⌕ <span>搜索 Agent、任务、文档…</span><kbd>Ctrl + K</kbd></Link>
          <Link to="/notifications" className="home-dashboard-avatar" aria-label="通知与个人入口">A</Link>
        </div>
      </header>
      {tasksQuery.isError && <p className="home-dashboard-warning" role="status">任务数据暂不可用，指标显示为 0 或暂无数据。</p>}
      <div className="home-kpi-grid">
        {metrics.map((metric) => <article className="home-kpi-card" key={metric.label}>
          <span className={`home-kpi-icon ${metric.tone}`}>{metric.icon}</span>
          <span className="home-kpi-label">{metric.label}</span>
          <strong>{tasksQuery.isPending ? '…' : metric.value}</strong>
          <small>{metric.note}</small>
        </article>)}
      </div>
      <TrendChart points={points} loading={trendsQuery.isPending} failed={trendsQuery.isError} />
      <p className="home-dashboard-footnote">指标仅使用服务端返回数据；耗时或成本字段缺失时显示“暂无数据”，不生成模拟值。进行中任务：{formatCount(tasksQuery.isSuccess ? inProgress : undefined)}</p>
    </section>
  )
}
