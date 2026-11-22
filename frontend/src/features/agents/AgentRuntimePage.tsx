import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { EmptyState, ErrorState, LoadingState } from '@/components/States'
import { apiClient } from '@/lib/api'
import './agent-runtime.css'
import { unwrapList } from '@/features/tasks/task-model'
import type { TaskRecord } from '@/features/tasks/task-model'

type RegistryAgent = {
  kind: string
  item_id: string
  version: number
  definition: Record<string, unknown>
  status: 'draft' | 'published' | 'disabled' | string
  created_by: string
  created_at: string
  published_at: string | null
}
type RegistryResponse = { items: RegistryAgent[] }
type RegistryDetail = RegistryAgent
type WorkbenchTask = Pick<TaskRecord, 'task_id' | 'run_id' | 'agent_id' | 'status' | 'created_at' | 'updated_at'>

const displayName = (item: RegistryAgent) => {
  const definition = item.definition
  const manifest = definition.manifest && typeof definition.manifest === 'object'
    ? definition.manifest as Record<string, unknown>
    : undefined
  const value = definition.name ?? definition.display_name ?? manifest?.name
  return typeof value === 'string' && value.trim() ? value : item.item_id
}
const description = (item: RegistryAgent) => {
  const definition = item.definition
  const manifest = definition.manifest && typeof definition.manifest === 'object'
    ? definition.manifest as Record<string, unknown>
    : undefined
  const value = definition.description ?? manifest?.description
  return typeof value === 'string' ? value : 'Registry definition has no description.'
}
const formatTime = (value: string | null | undefined) => {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}
const statusLabel: Record<string, string> = {
  published: '已发布',
  draft: '草稿',
  disabled: '已停用',
}
const statusClass: Record<string, string> = {
  published: 'is-published',
  draft: 'is-draft',
  disabled: 'is-disabled',
}

export function AgentRuntimeList() {
  const [search, setSearch] = useState('')
  const query = useQuery({
    queryKey: ['agent-runtime-registry'],
    queryFn: () => apiClient.get<RegistryResponse>('/platform/v1/registry/agent?limit=100'),
    refetchInterval: 30_000,
  })
  const tasks = useQuery({
    queryKey: ['agent-runtime-workbench-tasks'],
    queryFn: async () => unwrapList<WorkbenchTask>(await apiClient.get<unknown>('/platform/v1/workbench/tasks')),
    refetchInterval: 15_000,
  })
  const latestRunByAgent = useMemo(() => {
    const latest = new Map<string, WorkbenchTask>()
    for (const task of tasks.data ?? []) {
      if (!task.agent_id || !task.run_id) continue
      const current = latest.get(task.agent_id)
      const timestamp = Date.parse(task.updated_at ?? task.created_at ?? '') || 0
      const currentTimestamp = Date.parse(current?.updated_at ?? current?.created_at ?? '') || 0
      if (!current || timestamp > currentTimestamp) latest.set(task.agent_id, task)
    }
    return latest
  }, [tasks.data])
  const items = useMemo(() => {
    const rows = query.data?.items ?? []
    const needle = search.trim().toLowerCase()
    return needle ? rows.filter((item) => [item.item_id, displayName(item), description(item), item.status]
      .some((value) => value.toLowerCase().includes(needle))) : rows
  }, [query.data?.items, search])

  return (
    <section className="page-content agent-runtime-page">
      <header className="page-heading agent-runtime-heading">
        <div><p className="eyebrow">AGENT OPERATIONS / REGISTRY</p><h1>Agent 运行态</h1><p>按当前身份与租户权限读取 Registry 中的 Agent 定义及发布状态。</p></div>
        <span className="agent-runtime-source">REGISTRY · READ ONLY</span>
      </header>
      <div className="agent-runtime-toolbar">
        <label>筛选 Agent<input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="名称、ID、描述或状态" /></label>
        <span>{items.length} / {query.data?.items.length ?? 0} 个 Agent</span>
      </div>
      {query.isPending ? <LoadingState label="正在读取 Agent Registry" /> : query.isError ? (
        <ErrorState title="Agent Registry 暂不可用" description={query.error instanceof Error ? query.error.message : '读取 Agent 列表失败'} onRetry={() => void query.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState title="暂无 Agent 定义" description="Registry 当前没有返回匹配记录；不会使用本地演示数据补齐列表。" />
      ) : (
        <div className="agent-runtime-table-wrap"><table className="agent-runtime-table"><thead><tr><th>Agent</th><th>Registry 状态</th><th>版本</th><th>最近发布</th><th>健康状态</th><th>最近 Run</th><th>最近任务状态</th></tr></thead><tbody>
          {items.map((item) => { const latest = latestRunByAgent.get(item.item_id); return <tr key={item.item_id}>
            <td><Link to={`/operations/agents/${encodeURIComponent(item.item_id)}`}><strong>{displayName(item)}</strong><small>{item.item_id}</small></Link><p>{description(item)}</p></td>
            <td><span className={`agent-runtime-status ${statusClass[item.status] ?? ''}`}>{statusLabel[item.status] ?? item.status}</span></td>
            <td>v{item.version}</td><td>{formatTime(item.published_at)}</td>
            <td><span className="agent-runtime-unavailable">未知 · 后端未提供健康探针</span></td>
            <td>{latest?.run_id ? <Link to={`/operations/runs/${encodeURIComponent(latest.run_id)}`}>{latest.run_id}</Link> : <span className="agent-runtime-unavailable">暂无 Run</span>}</td>
            <td>{latest ? latest.status : '暂无任务'}</td>
          </tr> })}
        </tbody></table></div>
      )}
      <p className="agent-runtime-footnote">每 30 秒刷新，最多读取 100 条 Registry 记录。健康状态 API 尚未提供，因此明确显示未知；最近 Run 由 Workbench Task 的真实 agent_id/run_id 关联（15 秒刷新）。</p>
    </section>
  )
}

export function AgentRuntimeDetail() {
  const { agentId = '' } = useParams()
  const query = useQuery({
    queryKey: ['agent-runtime-registry-detail', agentId],
    queryFn: () => apiClient.get<RegistryDetail>(`/platform/v1/registry/agent/${encodeURIComponent(agentId)}/active`),
    enabled: Boolean(agentId),
  })
  const item = query.data

  return (
    <section className="page-content agent-runtime-page">
      <header className="page-heading"><p className="eyebrow">AGENT OPERATIONS / DETAIL</p><h1>Agent 运行详情</h1><p>展示 Registry active version；运行健康与最近 Run 需后端提供专用查询接口。</p></header>
      <Link className="agent-runtime-back" to="/operations/agents">← 返回 Agent 列表</Link>
      {query.isPending ? <LoadingState label="正在读取 Agent active version" /> : query.isError ? (
        <ErrorState title="Agent 详情不可用" description={query.error instanceof Error ? query.error.message : '读取 Agent active version 失败'} onRetry={() => void query.refetch()} />
      ) : !item ? <EmptyState title="Agent 未找到 active version" description="该 Agent 可能尚未发布，或当前租户无权读取。" /> : (
        <article className="agent-runtime-detail">
          <div className="agent-runtime-detail-header"><div><span className={`agent-runtime-status ${statusClass[item.status] ?? ''}`}>{statusLabel[item.status] ?? item.status}</span><h2>{displayName(item)}</h2><p>{item.item_id} · v{item.version}</p></div><time>{formatTime(item.published_at)}</time></div>
          <p className="agent-runtime-description">{description(item)}</p>
          <dl><div><dt>Registry kind</dt><dd>{item.kind}</dd></div><div><dt>创建者</dt><dd>{item.created_by || '—'}</dd></div><div><dt>创建时间</dt><dd>{formatTime(item.created_at)}</dd></div><div><dt>发布时间</dt><dd>{formatTime(item.published_at)}</dd></div><div><dt>健康状态</dt><dd>后端未提供该 Agent 的健康状态 API</dd></div><div><dt>最近 Run</dt><dd>后端未提供按 Agent 聚合的最近 Run API</dd></div></dl>
          <h3>Active definition</h3><pre>{JSON.stringify(item.definition, null, 2)}</pre>
        </article>
      )}
    </section>
  )
}
