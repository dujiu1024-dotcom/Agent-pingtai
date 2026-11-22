import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { apiClient } from '@/lib/api'
import { unwrapList } from '@/features/tasks/task-model'
import type { TaskRecord } from '@/features/tasks/task-model'

type AnyRecord = Record<string, unknown>
type TrendPoint = {
  label: string
  tasks?: number
  success?: number
}

function record(value: unknown): AnyRecord {
  return value && typeof value === 'object' ? (value as AnyRecord) : {}
}

function numberAt(source: AnyRecord, keys: string[]): number | undefined {
  for (const key of keys) {
    const value = source[key]
    if (typeof value === 'number' && Number.isFinite(value)) return value
    if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) {
      return Number(value)
    }
  }
  return undefined
}

function listAt(value: unknown): unknown[] {
  if (Array.isArray(value)) return value
  const root = record(value)
  const data = record(root.data)
  for (const candidate of [
    root.items,
    root.points,
    root.trends,
    root.series,
    data.items,
    data.points,
    data.trends,
    data.series,
  ]) {
    if (Array.isArray(candidate)) return candidate
  }
  if (Array.isArray(root.data)) return root.data
  return []
}

function dateLabel(value: unknown, index: number): string {
  if (typeof value !== 'string' && typeof value !== 'number') return String(index + 1)
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? String(value)
    : `${date.getMonth() + 1}/${date.getDate()}`
}

function trendPoints(value: unknown): TrendPoint[] {
  return listAt(value).map((item, index) => {
    const point = record(item)
    return {
      label: dateLabel(
        point.date ?? point.label ?? point.timestamp ?? point.created_at,
        index,
      ),
      tasks: numberAt(point, ['task_count', 'tasks', 'count', 'total']),
      success: numberAt(point, [
        'success_rate',
        'successRate',
        'success_rate_percent',
        'success',
      ]),
    }
  })
}

function formatCount(value: number | undefined): string {
  return value == null ? '暂无数据' : Math.round(value).toLocaleString('zh-CN')
}

function formatPercent(value: number | undefined): string {
  if (value == null) return '暂无数据'
  return `${(value <= 1 ? value * 100 : value).toFixed(1)}%`
}

function formatDuration(value: number | undefined): string {
  if (value == null) return '暂无数据'
  return value >= 1000 ? `${(value / 1000).toFixed(1)}s` : `${Math.round(value)}ms`
}

function formatCost(value: number | undefined): string {
  return value == null ? '暂无数据' : `¥${value.toFixed(2)}`
}

function linePath(
  points: TrendPoint[],
  key: 'tasks' | 'success',
  xAt: (index: number) => number,
  yAt: (value: number) => number,
): string {
  let drawing = false
  return points
    .map((point, index) => {
      const value = point[key]
      if (value == null) {
        drawing = false
        return ''
      }
      const command = drawing ? 'L' : 'M'
      drawing = true
      return `${command} ${xAt(index)} ${yAt(value)}`
    })
    .filter(Boolean)
    .join(' ')
}

function TrendChart({
  points,
  loading,
  failed,
}: {
  points: TrendPoint[]
  loading: boolean
  failed: boolean
}) {
  const width = 760
  const height = 230
  const left = 42
  const right = 42
  const top = 18
  const bottom = 34
  const plotWidth = width - left - right
  const plotHeight = height - top - bottom
  const taskValues = points.flatMap((point) =>
    point.tasks == null ? [] : [point.tasks],
  )
  const maxTasks = Math.max(1, ...taskValues)
  const xAt = (index: number) =>
    left +
    (points.length < 2 ? plotWidth / 2 : (index / (points.length - 1)) * plotWidth)
  const taskY = (value: number) =>
    top + plotHeight - (value / maxTasks) * plotHeight
  const successY = (value: number) => {
    const normalized = value > 1 ? value / 100 : value
    return top + plotHeight - Math.max(0, Math.min(1, normalized)) * plotHeight
  }
  const taskPath = linePath(points, 'tasks', xAt, taskY)
  const successPath = linePath(points, 'success', xAt, successY)
  const hasTaskData = taskValues.length > 0
  const hasSuccessData = points.some((point) => point.success != null)

  return (
    <div className="dashboard-chart">
      <div className="dashboard-chart-legend">
        <span>
          <i className="dashboard-legend-task" />
          任务数
        </span>
        <span>
          <i className="dashboard-legend-success" />
          成功率
        </span>
      </div>
      {loading ? (
        <div className="dashboard-chart-state">正在加载趋势数据…</div>
      ) : failed ? (
        <div className="dashboard-chart-state">趋势数据暂不可用</div>
      ) : points.length === 0 || (!hasTaskData && !hasSuccessData) ? (
        <div className="dashboard-chart-state">暂无趋势数据</div>
      ) : (
        <svg
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label="任务数与成功率趋势图，左轴为任务数，右轴为成功率"
        >
          {[0, 1, 2, 3].map((tick) => {
            const y = top + (plotHeight / 3) * tick
            return (
              <g key={tick}>
                <line
                  x1={left}
                  x2={width - right}
                  y1={y}
                  y2={y}
                  className="dashboard-grid-line"
                />
                <text
                  x={left - 10}
                  y={y + 4}
                  textAnchor="end"
                  className="dashboard-axis-label"
                >
                  {Math.round(maxTasks * (1 - tick / 3))}
                </text>
                <text
                  x={width - right + 10}
                  y={y + 4}
                  textAnchor="start"
                  className="dashboard-axis-label"
                >
                  {`${Math.round(100 - (tick / 3) * 100)}%`}
                </text>
              </g>
            )
          })}
          {hasTaskData && <path d={taskPath} className="dashboard-line-task" />}
          {hasSuccessData && (
            <path d={successPath} className="dashboard-line-success" />
          )}
          {points.map((point, index) => {
            const x = xAt(index)
            return (
              <g key={`${point.label}-${index}`}>
                {point.tasks != null && (
                  <circle
                    cx={x}
                    cy={taskY(point.tasks)}
                    r="3.5"
                    className="dashboard-dot-task"
                  />
                )}
                {point.success != null && (
                  <circle
                    cx={x}
                    cy={successY(point.success)}
                    r="3.5"
                    className="dashboard-dot-success"
                  />
                )}
                <text
                  x={x}
                  y={height - 8}
                  textAnchor="middle"
                  className="dashboard-axis-label"
                >
                  {point.label}
                </text>
              </g>
            )
          })}
        </svg>
      )}
    </div>
  )
}

export function Dashboard() {
  const tasksQuery = useQuery({
    queryKey: ['dashboard', 'workbench-tasks'],
    queryFn: () => apiClient.get<unknown>('/workbench/tasks'),
    refetchInterval: 30000,
  })
  const trendsQuery = useQuery({
    queryKey: ['dashboard', 'observability-trends'],
    queryFn: () => apiClient.get<unknown>('/observability/trends'),
    refetchInterval: 30000,
  })
  const taskPayload = record(tasksQuery.data)
  const taskData = record(taskPayload.data)
  const tasks = useMemo(
    () => unwrapList<TaskRecord>(tasksQuery.data),
    [tasksQuery.data],
  )
  const completed = tasks.filter((task) =>
    ['completed', 'succeeded'].includes(task.status.toLowerCase()),
  ).length
  const successRate =
    numberAt(taskPayload, ['success_rate', 'successRate']) ??
    numberAt(taskData, ['success_rate', 'successRate']) ??
    (tasks.length ? completed / tasks.length : undefined)
  const totalTasks =
    numberAt(taskPayload, ['total_tasks', 'task_count', 'total']) ??
    numberAt(taskData, ['total_tasks', 'task_count', 'total']) ??
    (tasksQuery.isSuccess ? tasks.length : undefined)
  const averageDuration =
    numberAt(taskPayload, [
      'average_duration_ms',
      'avg_duration_ms',
      'average_duration',
      'avg_duration',
    ]) ??
    numberAt(taskData, [
      'average_duration_ms',
      'avg_duration_ms',
      'average_duration',
      'avg_duration',
    ])
  const totalCost =
    numberAt(taskPayload, ['total_cost', 'cost']) ??
    numberAt(taskData, ['total_cost', 'cost'])
  const points = useMemo(() => trendPoints(trendsQuery.data), [trendsQuery.data])
  const metrics = [
    {
      label: '任务数',
      value: formatCount(totalTasks),
      icon: '▶',
      tone: 'blue',
      hint: '任务总量',
    },
    {
      label: '成功率',
      value: formatPercent(successRate),
      icon: '✓',
      tone: 'green',
      hint: '成功任务占比',
    },
    {
      label: '平均耗时',
      value: formatDuration(averageDuration),
      icon: '◔',
      tone: 'violet',
      hint: '平均任务执行时长',
    },
    {
      label: '总成本',
      value: formatCost(totalCost),
      icon: '¥',
      tone: 'orange',
      hint: '累计任务成本',
    },
  ]

  return (
    <section className="page-content dashboard-page dashboard-redesign">
      <div className="dashboard-heading">
        <div>
          <p className="eyebrow">AGENT PLATFORM / WORKSPACE</p>
          <h1>欢迎回来！</h1>
          <p>让 AI 赋能每一个工作场景</p>
        </div>
        <div className="dashboard-heading-actions">
          <Link to="/search" className="dashboard-search">
            <span aria-hidden="true">⌕</span>
            搜索 Agent、任务、文档…
            <kbd>Ctrl + K</kbd>
          </Link>
          <Link
            to="/notifications"
            className="dashboard-avatar"
            aria-label="通知与个人入口"
          >
            A
          </Link>
        </div>
      </div>

      <div className="dashboard-kpi-grid">
        {metrics.map((metric) => (
          <article className="dashboard-kpi-card" key={metric.label}>
            <span className={`dashboard-kpi-icon ${metric.tone}`}>
              {metric.icon}
            </span>
            <span className="dashboard-kpi-label">{metric.label}</span>
            <strong>
              {tasksQuery.isPending &&
              metric.label !== '平均耗时' &&
              metric.label !== '总成本'
                ? '…'
                : metric.value}
            </strong>
            <small>{metric.hint}</small>
          </article>
        ))}
      </div>

      <div className="dashboard-main-grid">
        <section className="dashboard-panel dashboard-trend-panel">
          <header className="dashboard-panel-heading">
            <div>
              <h2>系统概览</h2>
              <p>任务数与成功率趋势</p>
            </div>
            <span className="dashboard-live-state">
              <i />
              趋势统计
            </span>
          </header>
          <TrendChart
            points={points}
            loading={trendsQuery.isPending}
            failed={trendsQuery.isError}
          />
        </section>
        <section className="dashboard-panel dashboard-work-panel">
          <header className="dashboard-panel-heading">
            <div>
              <h2>我的工作台</h2>
              <p>最近任务与执行状态</p>
            </div>
            <Link to="/workspace/tasks">查看全部 →</Link>
          </header>
          {tasksQuery.isPending ? (
            <div className="dashboard-work-empty">正在加载任务…</div>
          ) : tasksQuery.isError ? (
            <div className="dashboard-work-empty">任务数据暂不可用</div>
          ) : tasks.length === 0 ? (
            <div className="dashboard-work-empty">
              暂无任务，创建任务后将在这里显示
            </div>
          ) : (
            <div className="dashboard-task-list">
              {tasks.slice(0, 3).map((task) => (
                <Link
                  className="dashboard-task-row"
                  to={`/operations/tasks/${encodeURIComponent(task.task_id)}`}
                  key={task.task_id}
                >
                  <span
                    className="dashboard-task-status"
                    data-status={task.status.toLowerCase()}
                  />
                  <span className="dashboard-task-copy">
                    <strong>{task.title}</strong>
                    <small>{task.status}</small>
                  </span>
                  <span className="dashboard-task-arrow">→</span>
                </Link>
              ))}
            </div>
          )}
        </section>
      </div>
      <p className="dashboard-data-note">
        {tasksQuery.isError
          ? '任务接口请求失败；请检查服务状态。'
          : '仅展示后端已返回的指标；缺少成本或耗时字段时显示“暂无数据”，不填充演示值。'}
      </p>
    </section>
  )
}
