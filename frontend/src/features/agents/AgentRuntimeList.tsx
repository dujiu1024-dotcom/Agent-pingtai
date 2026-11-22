import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { ErrorState, LoadingState } from '@/components/States'
import { apiClient } from '@/lib/api/api-client'
import { valueLabel } from '@/lib/i18n/labels'
import { unwrapList } from '@/features/tasks/task-model'

type AgentRegistryItem = {
  item_id: string
  version: number
  status: string
  definition?: { name?: string; description?: string }
  created_at?: string
  published_at?: string | null
}

type TaskRecord = {
  task_id: string
  run_id?: string
  agent_id?: string
  status: string
  updated_at?: string
  created_at?: string
}

const REGISTRY = '/platform/v1/registry/agent?limit=100'
const TASKS = '/platform/v1/workbench/tasks'
const dateLabel = (value?: string) =>
  value ? new Date(value).toLocaleString('zh-CN') : '—'

export function AgentRuntimeList() {
  const [filter, setFilter] = useState('')
  const agents = useQuery({
    queryKey: ['agent-runtime', 'registry'],
    queryFn: async () =>
      unwrapList<AgentRegistryItem>(await apiClient.get<unknown>(REGISTRY)),
    refetchInterval: 30000,
    retry: false,
  })
  const tasks = useQuery({
    queryKey: ['agent-runtime', 'tasks'],
    queryFn: async () =>
      unwrapList<TaskRecord>(await apiClient.get<unknown>(TASKS)),
    refetchInterval: 15000,
    retry: false,
  })

  const latestByAgent = useMemo(() => {
    const latest = new Map<string, TaskRecord>()
    for (const task of tasks.data ?? []) {
      if (!task.agent_id) continue
      const current = latest.get(task.agent_id)
      const taskTime = Date.parse(task.updated_at ?? task.created_at ?? '') || 0
      const currentTime = Date.parse(current?.updated_at ?? current?.created_at ?? '') || 0
      if (!current || taskTime > currentTime) latest.set(task.agent_id, task)
    }
    return latest
  }, [tasks.data])

  const visibleAgents = useMemo(() => {
    const needle = filter.trim().toLowerCase()
    return (agents.data ?? []).filter((agent) => {
      if (!needle) return true
      return `${agent.item_id} ${agent.definition?.name ?? ''} ${agent.definition?.description ?? ''}`
        .toLowerCase()
        .includes(needle)
    })
  }, [agents.data, filter])

  return (
    <section className="page-content">
      <div className="page-heading">
        <p className="eyebrow">智能体运营 · #808</p>
        <h1>Agent 运行态</h1>
        <p>基于租户隔离的 Agent Registry 与 Workbench Task 数据展示状态和最近执行记录。</p>
      </div>

      <div className="task-toolbar">
        <input
          aria-label="搜索 Agent"
          placeholder="搜索 Agent ID / 名称"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        />
        <span className="detail-empty">Registry 30 秒刷新 · 最近任务 15 秒刷新</span>
      </div>

      {agents.isPending && <LoadingState label="正在加载 Agent Registry" />}
      {agents.isError && (
        <ErrorState
          title="Agent Registry 加载失败"
          description={agents.error instanceof Error ? agents.error.message : '请求失败'}
          onRetry={() => void agents.refetch()}
        />
      )}
      {agents.data && tasks.isError && (
        <ErrorState
          title="最近 Run 数据不可用"
          description={tasks.error instanceof Error ? tasks.error.message : 'Workbench Task 请求失败'}
          onRetry={() => void tasks.refetch()}
        />
      )}
      {agents.data && visibleAgents.length === 0 && (
        <p className="detail-empty">
          {agents.data.length === 0 ? '当前租户没有 Agent Registry 记录。' : '没有匹配的 Agent。'}
        </p>
      )}
      {agents.data && visibleAgents.length > 0 && (
        <div className="generic-table-wrap">
          <table className="generic-table">
            <thead>
              <tr>
                <th>Agent</th>
                <th>Registry 状态</th>
                <th>健康</th>
                <th>最近 Run</th>
                <th>最近任务状态</th>
                <th>最近活动</th>
              </tr>
            </thead>
            <tbody>
              {visibleAgents.map((agent) => {
                const latest = latestByAgent.get(agent.item_id)
                return (
                  <tr key={agent.item_id}>
                    <td>
                      <Link to={`/operations/agents/${encodeURIComponent(agent.item_id)}`}>
                        {agent.definition?.name || agent.item_id}
                      </Link>
                      <small className="cell-secondary">{agent.item_id} · v{agent.version}</small>
                    </td>
                    <td>
                      <span className={`registry-status registry-status--${agent.status}`}>
                        {valueLabel(agent.status)}
                      </span>
                    </td>
                    <td>
                      <span className="status-badge badge-default">未知</span>
                      <small className="cell-secondary">后端未提供 Agent 健康探针</small>
                    </td>
                    <td>
                      {latest?.run_id ? (
                        <Link to={`/operations/runs/${encodeURIComponent(latest.run_id)}`}>
                          {latest.run_id}
                        </Link>
                      ) : (
                        <em className="cell-empty">暂无 Run</em>
                      )}
                    </td>
                    <td>{latest ? valueLabel(latest.status) : '暂无任务'}</td>
                    <td>{dateLabel(latest?.updated_at ?? latest?.created_at)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
