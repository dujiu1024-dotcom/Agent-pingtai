import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQueries, useQuery } from '@tanstack/react-query'

import { EmptyState, ErrorState, LoadingState } from '@/components/States'
import { apiClient } from '@/lib/api'
import {
  filterRunEvents,
  resolveRunId,
  resolveTaskTitle,
  unwrapList,
} from './task-model'
import type { RunEvent, TaskRecord } from './task-model'
import { buildTaskRunTimelineEntries } from './task-run-timeline-model'
import './task-controls.css'

const TASKS_API = '/platform/v1/workbench/tasks'
const RUNS_API = '/platform/v1/runs'

/** Run timeline backed exclusively by persisted Run events. */
export function TaskRunTimeline() {
  const [eventType, setEventType] = useState('all')
  const [search, setSearch] = useState('')
  const tasksQuery = useQuery({
    queryKey: ['tasks', 'run-event-timeline'],
    queryFn: async () =>
      unwrapList<TaskRecord>(await apiClient.get<unknown>(TASKS_API)),
    refetchInterval: 15000,
  })
  const tasks = tasksQuery.data ?? []
  const runs = useMemo(() => {
    const seen = new Set<string>()
    return tasks.flatMap((task) => {
      const runId = resolveRunId(task)
      if (!runId || seen.has(runId)) return []
      seen.add(runId)
      return [{ task, runId }]
    })
  }, [tasks])
  const eventQueries = useQueries({
    queries: runs.map(({ runId }) => ({
      queryKey: ['run-events', runId],
      queryFn: async () =>
        unwrapList<RunEvent>(
          await apiClient.get<unknown>(
            `${RUNS_API}/${encodeURIComponent(runId)}/events`,
          ),
        ),
      refetchInterval: 10000,
    })),
  })
  const entries = useMemo(
    () =>
      buildTaskRunTimelineEntries(
        runs,
        eventQueries.map((query) => query.data ?? []),
      ),
    [eventQueries, runs],
  )
  const filtered = useMemo(
    () =>
      entries.filter(
        (entry) =>
          filterRunEvents([entry.event], eventType, search).length > 0,
      ),
    [entries, eventType, search],
  )
  const eventTypes = [
    ...new Set(
      entries.map(({ event }) => event.event_type ?? event.type),
    ),
  ].sort()
  const loading =
    tasksQuery.isPending || eventQueries.some((query) => query.isPending)
  const failures = eventQueries.filter((query) => query.isError).length

  if (tasksQuery.isPending) {
    return <LoadingState label="正在加载任务与 Run 事件" />
  }
  if (tasksQuery.isError) {
    return (
      <ErrorState
        title="Run 事件时间线加载失败"
        description={
          tasksQuery.error instanceof Error
            ? tasksQuery.error.message
            : '任务列表请求失败'
        }
        onRetry={() => void tasksQuery.refetch()}
      />
    )
  }

  return (
    <section className="page-content task-center">
      <div className="page-heading task-heading">
        <div>
          <p className="eyebrow">工作空间 / 任务运营</p>
          <h1>Run 事件时间线</h1>
          <p>
            按真实 Run 事件发生时间汇总任务执行过程；不生成模拟事件或进度。
          </p>
        </div>
        <Link className="button-secondary" to="/workspace/tasks">
          返回任务中心
        </Link>
      </div>
      <div
        className="task-toolbar"
        role="search"
        aria-label="筛选 Run 事件"
      >
        <select
          aria-label="按事件类型筛选"
          value={eventType}
          onChange={(event) => setEventType(event.target.value)}
        >
          <option value="all">全部事件类型</option>
          {eventTypes.map((type) => (
            <option key={type} value={type}>
              {type}
            </option>
          ))}
        </select>
        <input
          type="search"
          aria-label="搜索 Run 事件"
          placeholder="搜索事件、摘要、操作人或 Trace ID"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <span>
          {filtered.length} 条事件 · {runs.length} 个 Run
        </span>
      </div>
      {failures > 0 && (
        <p className="task-bulk-warning" role="status">
          {failures} 个 Run 的事件请求失败；其余成功返回的事件仍可查看。
        </p>
      )}
      {loading && <LoadingState label="正在补齐 Run 事件" />}
      {!filtered.length && !loading ? (
        <EmptyState
          title={entries.length ? '没有匹配的 Run 事件' : '暂无 Run 执行事件'}
          description={
            entries.length
              ? '调整事件类型或关键词筛选条件。'
              : '任务没有关联 Run，或服务端尚未记录事件。'
          }
        />
      ) : (
        <div className="task-timeline">
          {filtered.map(({ task, runId, event }) => (
            <article key={`${runId}:${event.event_id}`}>
              <time>
                {event.occurred_at
                  ? new Date(event.occurred_at).toLocaleString()
                  : '时间未提供'}
              </time>
              <div>
                <strong>{event.event_type ?? event.type}</strong>
                <p>{event.summary ?? '服务端未提供事件摘要'}</p>
                <span>
                  <Link
                    to={`/operations/tasks/${encodeURIComponent(task.task_id)}`}
                  >
                    {resolveTaskTitle(task)}
                  </Link>
                  {' · '}
                  <Link to={`/operations/runs/${encodeURIComponent(runId)}`}>
                    Run {runId.slice(-8)}
                  </Link>
                  {event.actor && ` · ${event.actor}`}
                  {event.trace_id && <> · Trace {event.trace_id}</>}
                </span>
                {event.sequence != null && (
                  <small>序号 #{event.sequence}</small>
                )}
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  )
}
