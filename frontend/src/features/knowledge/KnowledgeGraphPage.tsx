import { useState } from 'react'
import type { FormEvent } from 'react'
import { useQuery } from '@tanstack/react-query'

import { EmptyState, ErrorState, LoadingState } from '@/components/States'
import { ApiError, apiClient } from '@/lib/api'
import { localizedJson, valueLabel } from '@/lib/i18n/labels'
import './KnowledgeGraphPage.css'

type GraphEntity = {
  entity_id: string
  entity_type: string
  name: string
  attributes: Record<string, unknown>
  project_id: string | null
  source_ref: {
    domain: string
    source_type: string
    source_id: string
    source_version?: string | null
  } | null
}

type GraphRelation = {
  relation_id: string
  relation_type: string
  source_entity_id: string
  target_entity_id: string
  project_id: string | null
  attributes: Record<string, unknown>
}

type EntityResponse = { items: GraphEntity[]; limit: number }
type NeighborResponse = {
  entities: GraphEntity[]
  relations: GraphRelation[]
  truncated: boolean
  visited_count: number
}
type MetricsResponse = {
  query: string
  sampled: number
  entity_count: number
  relation_count: number
  isolated_entity_count: number
  isolated_entity_ratio: number
  relation_density: number
  active_subgraph_count: number
  truncated: boolean
}

type ExtractionJob = {
  job_id: string
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  total: number
  processed: number
  entity_count: number
  relation_count: number
  skipped: number
  error: string | null
  created_at: string
  updated_at: string
}

const JOB_STATUS: Record<ExtractionJob['status'], string> = {
  queued: '排队中',
  running: '抽取中',
  succeeded: '已完成',
  failed: '失败',
}

const ENTITY_TYPES = [
  ['', '全部类型'],
  ['concept', '概念'],
  ['person', '人物'],
  ['organization', '组织'],
  ['topic', '主题'],
  ['document', '文档'],
  ['memory', '记忆'],
  ['blackboard_entry', '黑板条目'],
  ['project', '项目'],
  ['user', '用户'],
] as const

const ENTITY_COLORS: Record<string, string> = {
  concept: '#6366f1',
  person: '#ec4899',
  organization: '#10b981',
  topic: '#0ea5e9',
  document: '#3b6cf6',
  memory: '#a78bfa',
  blackboard_entry: '#f59e0b',
  project: '#22c55e',
  user: '#f472b6',
}

const errorText = (error: unknown) => {
  if (error instanceof ApiError && error.status === 403)
    return '当前账号没有知识图谱读取权限（需要 knowledge.read 或 knowledge.manage）。'
  if (error instanceof ApiError && error.status === 503)
    return '服务端未启用知识图谱。'
  return error instanceof Error ? error.message : '请求失败，请稍后重试。'
}

const extractErrorText = (error: unknown) => {
  if (error instanceof ApiError && error.status === 503)
    return '服务端未启用知识图谱抽取，或本地模型不可用。'
  return errorText(error)
}

const short = (value: string, length = 12) =>
  value.length > length ? value.slice(0, length) + '…' : value

export function KnowledgeGraphPage() {
  const [query, setQuery] = useState('')
  const [entityType, setEntityType] = useState('')
  const [submitted, setSubmitted] = useState('')
  const [selectedId, setSelectedId] = useState('')
  const [extractLimit, setExtractLimit] = useState(10)
  const [starting, setStarting] = useState(false)
  const [extractError, setExtractError] = useState('')
  const [extractMessage, setExtractMessage] = useState('')

  const jobsQuery = useQuery({
    queryKey: ['graph', 'extractions'],
    queryFn: () =>
      apiClient.get<{ items: ExtractionJob[] }>('/platform/v1/graph/extractions?limit=1'),
    refetchInterval: 5000,
    retry: false,
  })
  const latestJob = jobsQuery.data?.items?.[0]

  const startExtraction = async () => {
    setStarting(true)
    setExtractError('')
    setExtractMessage('')
    try {
      const result = await apiClient.post<{ job_id: string; total: number }>(
        '/platform/v1/graph/extractions',
        { limit: extractLimit },
      )
      setExtractMessage(
        result.total === 0
          ? '当前没有可抽取的知识文档，请先在知识库上传文档。'
          : `已启动抽取，共 ${result.total} 篇文档。`,
      )
      await jobsQuery.refetch()
    } catch (reason) {
      setExtractError(
        reason instanceof ApiError && reason.status === 403
          ? '当前账号没有知识库管理权限，无法启动抽取。'
          : extractErrorText(reason),
      )
    } finally {
      setStarting(false)
    }
  }

  const entitiesQuery = useQuery({
    queryKey: ['graph', 'entities', submitted, entityType],
    enabled: Boolean(submitted),
    queryFn: () => {
      const params = new URLSearchParams({ q: submitted, limit: '50' })
      if (entityType) params.set('entity_type', entityType)
      return apiClient.get<EntityResponse>(`/platform/v1/graph/entities?${params}`)
    },
    retry: false,
  })

  const metricsQuery = useQuery({
    queryKey: ['graph', 'metrics', submitted],
    enabled: Boolean(submitted),
    queryFn: () =>
      apiClient.get<MetricsResponse>(
        `/platform/v1/graph/metrics?${new URLSearchParams({ q: submitted, limit: '100' })}`,
      ),
    retry: false,
  })

  const neighborsQuery = useQuery({
    queryKey: ['graph', 'neighbors', selectedId],
    enabled: Boolean(selectedId),
    queryFn: () =>
      apiClient.get<NeighborResponse>(
        `/platform/v1/graph/entities/${encodeURIComponent(selectedId)}/neighbors?${new URLSearchParams({
          direction: 'both',
          depth: '1',
          limit: '24',
        })}`,
      ),
    retry: false,
  })

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const value = query.trim()
    if (!value) return
    setSubmitted(value)
    setSelectedId('')
  }

  const entities = entitiesQuery.data?.items ?? []
  const selected = entities.find((entity) => entity.entity_id === selectedId) ?? null
  const neighbors = neighborsQuery.data
  const graphEntities = neighbors?.entities ?? (selected ? [selected] : [])
  const graphRelations = neighbors?.relations ?? []
  const names = new Map(graphEntities.map((entity) => [entity.entity_id, entity.name]))

  const positions = new Map<string, { x: number; y: number }>()
  const centerId = selected?.entity_id ?? graphEntities[0]?.entity_id
  if (centerId) {
    positions.set(centerId, { x: 330, y: 210 })
    const others = graphEntities.filter((entity) => entity.entity_id !== centerId)
    const radius = Math.max(120, Math.min(170, 40 + others.length * 9))
    others.forEach((entity, index) => {
      const angle = (index / Math.max(1, others.length)) * Math.PI * 2 - Math.PI / 2
      positions.set(entity.entity_id, {
        x: 330 + radius * Math.cos(angle),
        y: 210 + radius * Math.sin(angle),
      })
    })
  }

  return (
    <section className="page-content graph-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">智能体运营 / 知识图谱</p>
          <h1>知识图谱</h1>
          <p>检索实体、查看邻接关系与图谱质量指标。数据来自服务端图谱接口。</p>
        </div>
      </div>

      <section className="graph-extract">
        <div className="graph-extract-head">
          <div>
            <h2>图谱抽取</h2>
            <p>从知识库文档抽取概念、人物、组织与主题，并生成实体关系。</p>
          </div>
          <div className="graph-extract-actions">
            <label>
              抽取文档数
              <select
                value={extractLimit}
                onChange={(event) => setExtractLimit(Number(event.target.value))}
              >
                <option value={5}>最近 5 篇</option>
                <option value={10}>最近 10 篇</option>
                <option value={20}>最近 20 篇</option>
              </select>
            </label>
            <button
              className="button-primary"
              type="button"
              onClick={() => void startExtraction()}
              disabled={starting}
            >
              {starting ? '启动中…' : '开始抽取'}
            </button>
          </div>
        </div>
        {extractError && <p className="graph-extract-error" role="alert">{extractError}</p>}
        {extractMessage && <p className="graph-extract-success" role="status">{extractMessage}</p>}
        {latestJob && (
          <div className={`graph-job status-${latestJob.status}`}>
            <span className="graph-job-status">{JOB_STATUS[latestJob.status]}</span>
            <span>进度 {latestJob.processed} / {latestJob.total}</span>
            <span>实体 {latestJob.entity_count}</span>
            <span>关系 {latestJob.relation_count}</span>
            {latestJob.skipped > 0 && <span>跳过 {latestJob.skipped}</span>}
            {latestJob.error && <span className="graph-job-error">{latestJob.error}</span>}
          </div>
        )}
      </section>

      <form className="graph-search" onSubmit={submit}>
        <label className="graph-search-query">
          实体关键词
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="输入实体名称前缀，例如：合同、项目、Alice"
            maxLength={200}
          />
        </label>
        <label>
          实体类型
          <select value={entityType} onChange={(event) => setEntityType(event.target.value)}>
            {ENTITY_TYPES.map(([value, label]) => (
              <option key={value || 'all'} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <button className="button-primary" type="submit" disabled={!query.trim()}>
          检索实体
        </button>
      </form>

      {!submitted && (
        <EmptyState
          title="输入关键词开始检索"
          description="知识图谱按实体名称前缀检索。选择实体后可查看一跳邻居、关系列表与图谱指标。"
        />
      )}

      {submitted && entitiesQuery.isPending && <LoadingState label="正在检索实体" />}

      {submitted && entitiesQuery.isError && (
        <ErrorState
          title="图谱检索失败"
          description={errorText(entitiesQuery.error)}
          onRetry={() => void entitiesQuery.refetch()}
        />
      )}

      {submitted && entitiesQuery.isSuccess && entities.length === 0 && (
        <EmptyState
          title="没有匹配的实体"
          description="当前图谱中可能还没有数据，或没有实体名称以该关键词开头。知识抽取写入实体后会显示在这里。"
        />
      )}

      {submitted && entities.length > 0 && (
        <>
          <div className="graph-metrics">
            <article><span>实体数</span><strong>{metricsQuery.data?.entity_count ?? entities.length}</strong></article>
            <article><span>关系数</span><strong>{metricsQuery.data?.relation_count ?? '—'}</strong></article>
            <article><span>孤立实体</span><strong>{metricsQuery.data ? `${metricsQuery.data.isolated_entity_count}（${(metricsQuery.data.isolated_entity_ratio * 100).toFixed(1)}%）` : '—'}</strong></article>
            <article><span>关系密度</span><strong>{metricsQuery.data ? metricsQuery.data.relation_density.toFixed(3) : '—'}</strong></article>
            <article><span>连通子图</span><strong>{metricsQuery.data?.active_subgraph_count ?? '—'}</strong></article>
          </div>

          <div className="graph-layout">
            <aside className="graph-entities" aria-label="实体列表">
              <header>
                <h2>实体</h2>
                <span>共 {entities.length} 个</span>
              </header>
              <ul>
                {entities.map((entity) => (
                  <li key={entity.entity_id}>
                    <button
                      type="button"
                      className={entity.entity_id === selectedId ? 'active' : ''}
                      onClick={() => setSelectedId(entity.entity_id)}
                    >
                      <span
                        className="graph-dot"
                        style={{ background: ENTITY_COLORS[entity.entity_type] ?? '#94a3b8' }}
                      />
                      <span className="graph-entity-copy">
                        <strong>{entity.name}</strong>
                        <small>{valueLabel(entity.entity_type)}</small>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </aside>

            <section className="graph-canvas-card">
              {!selected ? (
                <EmptyState title="选择一个实体" description="从左侧选择实体，查看它的一跳邻居与关系。" />
              ) : neighborsQuery.isPending ? (
                <LoadingState label="正在读取关系" />
              ) : neighborsQuery.isError ? (
                <ErrorState
                  title="关系读取失败"
                  description={errorText(neighborsQuery.error)}
                  onRetry={() => void neighborsQuery.refetch()}
                />
              ) : (
                <>
                  <header className="graph-canvas-heading">
                    <div>
                      <h2>{selected.name}</h2>
                      <p>{valueLabel(selected.entity_type)} · 一跳邻居 {Math.max(0, graphEntities.length - 1)} 个</p>
                    </div>
                    {neighbors?.truncated && <span className="graph-truncated">结果已截断</span>}
                  </header>
                  <svg className="graph-canvas" viewBox="0 0 660 420" role="img" aria-label="实体关系图">
                    {graphRelations.map((relation) => {
                      const from = positions.get(relation.source_entity_id)
                      const to = positions.get(relation.target_entity_id)
                      if (!from || !to) return null
                      return (
                        <line
                          key={relation.relation_id}
                          x1={from.x}
                          y1={from.y}
                          x2={to.x}
                          y2={to.y}
                          stroke="#c7d2e5"
                          strokeWidth={1.5}
                        />
                      )
                    })}
                    {graphEntities.map((entity) => {
                      const point = positions.get(entity.entity_id)
                      if (!point) return null
                      const isCenter = entity.entity_id === centerId
                      return (
                        <g key={entity.entity_id}>
                          <circle
                            cx={point.x}
                            cy={point.y}
                            r={isCenter ? 16 : 11}
                            fill={ENTITY_COLORS[entity.entity_type] ?? '#94a3b8'}
                            stroke={isCenter ? '#1f2a44' : '#fff'}
                            strokeWidth={isCenter ? 3 : 2}
                          />
                          <text
                            x={point.x}
                            y={point.y + (isCenter ? 34 : 27)}
                            textAnchor="middle"
                            className="graph-node-label"
                          >
                            {short(entity.name)}
                          </text>
                        </g>
                      )
                    })}
                  </svg>

                  {graphRelations.length > 0 && (
                    <div className="graph-relations">
                      <h3>关系</h3>
                      <table>
                        <thead>
                          <tr>
                            <th>关系类型</th>
                            <th>源实体</th>
                            <th>目标实体</th>
                          </tr>
                        </thead>
                        <tbody>
                          {graphRelations.map((relation) => (
                            <tr key={relation.relation_id}>
                              <td>{valueLabel(relation.relation_type)}</td>
                              <td>{names.get(relation.source_entity_id) ?? '—'}</td>
                              <td>{names.get(relation.target_entity_id) ?? '—'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}

                  <details className="graph-attributes">
                    <summary>查看实体属性与来源</summary>
                    <pre>{localizedJson({ attributes: selected.attributes, source_ref: selected.source_ref })}</pre>
                  </details>
                </>
              )}
            </section>
          </div>
        </>
      )}
    </section>
  )
}
