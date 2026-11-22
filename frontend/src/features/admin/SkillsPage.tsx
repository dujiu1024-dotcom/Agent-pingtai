import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { ApiError, apiClient } from '@/lib/api'

type RegistryItem = {
  kind: string
  item_id: string
  version: number
  definition: Record<string, unknown>
  status: string
  created_by: string | null
  created_at: string
  published_at: string | null
}

type RegistryList = { items: RegistryItem[] }

function readable(value: unknown): string {
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return ''
}

function labelFor(item: RegistryItem): string {
  return readable(item.definition.name) || readable(item.definition.title) || item.item_id
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message
  return error instanceof Error ? error.message : 'Skills 数据读取失败'
}

export function SkillsPage() {
  const { skillId } = useParams<{ skillId: string }>()
  const [filter, setFilter] = useState('')
  const listQuery = useQuery({
    queryKey: ['skills-registry'],
    queryFn: () => apiClient.get<RegistryList>('/platform/v1/registry/skill?limit=100'),
    refetchInterval: 30_000,
    staleTime: 10_000,
  })
  const items = listQuery.data?.items ?? []
  const filtered = useMemo(() => {
    const needle = filter.trim().toLocaleLowerCase()
    if (!needle) return items
    return items.filter((item) =>
      [item.item_id, item.status, labelFor(item), JSON.stringify(item.definition)]
        .join(' ')
        .toLocaleLowerCase()
        .includes(needle),
    )
  }, [filter, items])
  const selected = skillId ? items.find((item) => item.item_id === skillId) : undefined
  const activeQuery = useQuery({
    queryKey: ['skill-active', skillId],
    queryFn: () => apiClient.get<RegistryItem>(`/platform/v1/registry/skill/${encodeURIComponent(skillId!)}/active`),
    enabled: Boolean(skillId),
    retry: false,
  })

  return (
    <section className="page-content">
      <div className="page-heading">
        <p className="eyebrow">PLATFORM MANAGEMENT / SKILLS</p>
        <h1>Skills 资源管理</h1>
        <p>读取租户 Registry 中已登记的 Skill 定义与 active 版本；此页面不创建或发布资源。</p>
      </div>
      <div className="ops-dashboard-grid">
        <section className="ops-panel">
          <div className="ops-panel-heading">
            <h2>Skill Registry</h2>
            <span>{items.length} 项 · 30 秒自动刷新</span>
          </div>
          <label className="field-label" htmlFor="skills-filter">筛选 Skill</label>
          <input id="skills-filter" value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="按名称、ID、状态或定义内容筛选" />
          {listQuery.isLoading ? <p className="muted">正在加载 Skills…</p> : null}
          {listQuery.isError ? (
            <div role="alert" className="ops-query-error">
              <span>{errorMessage(listQuery.error)}</span>
              <button type="button" onClick={() => void listQuery.refetch()}>重试</button>
            </div>
          ) : null}
          {!listQuery.isLoading && !listQuery.isError && filtered.length === 0 ? (
            <p className="muted">{items.length ? '没有匹配的 Skill。' : '当前 Registry 暂无 Skill。'}</p>
          ) : null}
          <div className="ops-ranking" role="list">
            {filtered.map((item) => (
              <div role="listitem" key={item.item_id} className="ops-panel-heading">
                <div>
                  <strong>{labelFor(item)}</strong>
                  <small>{item.item_id} · v{item.version} · {item.status}</small>
                </div>
                <Link className="text-link" to={`/admin/skills/${encodeURIComponent(item.item_id)}`}>查看详情</Link>
              </div>
            ))}
          </div>
        </section>
        <section className="ops-panel">
          <div className="ops-panel-heading"><h2>Skill 详情</h2><span>{skillId ?? '未选择'}</span></div>
          {!skillId ? <p className="muted">从左侧选择一个 Skill 查看定义与兼容性声明。</p> : null}
          {skillId && activeQuery.isLoading ? <p className="muted">正在读取 active 版本…</p> : null}
          {skillId && activeQuery.isError ? (
            <div role="alert" className="ops-query-error">
              <span>{errorMessage(activeQuery.error)}。该 Skill 可能没有 active 版本。</span>
              <button type="button" onClick={() => void activeQuery.refetch()}>重试</button>
            </div>
          ) : null}
          {activeQuery.data ? (
            <>
              <p><strong>{labelFor(activeQuery.data)}</strong></p>
              <p className="muted">版本 {activeQuery.data.version} · 状态 {activeQuery.data.status}</p>
              <dl>
                <dt>创建者</dt><dd>{activeQuery.data.created_by ?? '未提供'}</dd>
                <dt>创建时间</dt><dd>{new Date(activeQuery.data.created_at).toLocaleString()}</dd>
                <dt>发布时间</dt><dd>{activeQuery.data.published_at ? new Date(activeQuery.data.published_at).toLocaleString() : '未发布'}</dd>
              </dl>
              <h3>定义</h3>
              <pre className="code-preview">{JSON.stringify(activeQuery.data.definition, null, 2)}</pre>
              <h3>Agent 兼容性</h3>
              <p>{readable(activeQuery.data.definition.compatibility) || readable(activeQuery.data.definition.agent_compatibility) || '当前 Skill 定义未声明标准化 Agent 兼容性字段。'}</p>
            </>
          ) : null}
          {selected && !activeQuery.data && !activeQuery.isLoading && !activeQuery.isError ? (
            <p className="muted">列表项已加载，正在等待 active 版本信息。</p>
          ) : null}
          {skillId ? <Link className="text-link" to="/admin/skills">返回 Skills 列表</Link> : null}
        </section>
      </div>
    </section>
  )
}
