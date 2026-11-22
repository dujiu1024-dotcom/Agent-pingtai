import { useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, apiClient } from '@/lib/api'

type RegistryItem = {
  kind: string
  item_id: string
  version: number
  definition: Record<string, unknown>
  status: 'draft' | 'published' | 'disabled' | 'deprecated' | string
  created_by: string | null
  created_at: string
  published_at: string | null
  used_by?: string[]
}
type RegistryList = { items: RegistryItem[] }
type RegistryVersions = { items: RegistryItem[] }

const registryPath = '/platform/v1/registry/skill'
const itemPath = (id: string) => `${registryPath}/${encodeURIComponent(id)}`

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
  return error instanceof Error ? error.message : 'Skills 操作失败'
}
function compatibilityFor(definition: Record<string, unknown>): string {
  const compatibility = definition.agent_compatibility ?? definition.compatibility
  if (typeof compatibility === 'string') return compatibility
  if (compatibility && typeof compatibility === 'object') return JSON.stringify(compatibility, null, 2)
  return '此 Skill 未声明 Agent 兼容性元数据；不能据此推断兼容范围。'
}
function definitionText(item?: RegistryItem): string {
  return JSON.stringify(item?.definition ?? { name: '', description: '', agent_compatibility: [] }, null, 2)
}

export function SkillsPage() {
  const { skillId } = useParams<{ skillId: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [filter, setFilter] = useState('')
  const [newId, setNewId] = useState('')
  const [newDefinition, setNewDefinition] = useState('{"name":"","description":""}')
  const [editDefinition, setEditDefinition] = useState('')
  const [error, setError] = useState<string | null>(null)
  const listQuery = useQuery({
    queryKey: ['skills-registry'],
    queryFn: () => apiClient.get<RegistryList>(`${registryPath}?limit=100`),
    refetchInterval: 30_000,
    staleTime: 10_000,
  })
  const items = listQuery.data?.items ?? []
  const filtered = useMemo(() => {
    const needle = filter.trim().toLocaleLowerCase()
    if (!needle) return items
    return items.filter((item) =>
      [item.item_id, item.status, labelFor(item), JSON.stringify(item.definition)]
        .join(' ').toLocaleLowerCase().includes(needle),
    )
  }, [filter, items])
  const activeQuery = useQuery({
    queryKey: ['skill-active', skillId],
    queryFn: () => apiClient.get<RegistryItem>(`${itemPath(skillId!)}/active`),
    enabled: Boolean(skillId),
    retry: false,
  })
  const versionsQuery = useQuery({
    queryKey: ['skill-versions', skillId],
    queryFn: () => apiClient.get<RegistryVersions>(`${itemPath(skillId!)}/versions`),
    enabled: Boolean(skillId),
  })
  const invalidate = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['skills-registry'] }),
      queryClient.invalidateQueries({ queryKey: ['skill-active', skillId] }),
      queryClient.invalidateQueries({ queryKey: ['skill-versions', skillId] }),
    ])
  }
  const createDraft = useMutation({
    mutationFn: async () => {
      const id = newId.trim()
      if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(id)) throw new Error('Skill ID 格式无效')
      const definition = JSON.parse(newDefinition) as Record<string, unknown>
      if (!definition || Array.isArray(definition) || typeof definition !== 'object') throw new Error('定义必须是 JSON 对象')
      return apiClient.post<RegistryItem>(`${itemPath(id)}/versions`, { definition })
    },
    onSuccess: async (created) => {
      setNewId('')
      setError(null)
      await queryClient.invalidateQueries({ queryKey: ['skills-registry'] })
      navigate(`/admin/skills/${encodeURIComponent(created.item_id)}`)
    },
    onError: (reason) => setError(errorMessage(reason)),
  })
  const saveDraft = useMutation({
    mutationFn: async () => {
      if (!skillId) throw new Error('请先选择 Skill')
      const definition = JSON.parse(editDefinition) as Record<string, unknown>
      if (!definition || Array.isArray(definition) || typeof definition !== 'object') throw new Error('定义必须是 JSON 对象')
      return apiClient.post<RegistryItem>(`${itemPath(skillId)}/versions`, { definition })
    },
    onSuccess: async () => { setEditDefinition(''); setError(null); await invalidate() },
    onError: (reason) => setError(errorMessage(reason)),
  })
  const publish = useMutation({
    mutationFn: (version: number) => apiClient.post<RegistryItem>(`${itemPath(skillId!)}/versions/${version}/publish`),
    onSuccess: invalidate,
    onError: (reason) => setError(errorMessage(reason)),
  })
  const disable = useMutation({
    mutationFn: () => apiClient.post<RegistryItem>(`${itemPath(skillId!)}/disable`),
    onSuccess: invalidate,
    onError: (reason) => setError(errorMessage(reason)),
  })
  const rollback = useMutation({
    mutationFn: (version: number) => apiClient.post<RegistryItem>(`${itemPath(skillId!)}/rollback/${version}`),
    onSuccess: invalidate,
    onError: (reason) => setError(errorMessage(reason)),
  })
  const selected = skillId ? items.find((item) => item.item_id === skillId) : undefined
  const busy = createDraft.isPending || saveDraft.isPending || publish.isPending || disable.isPending || rollback.isPending

  return (
    <section className="page-content">
      <div className="page-heading">
        <p className="eyebrow">PLATFORM MANAGEMENT / SKILLS</p>
        <h1>Skills 资源管理</h1>
        <p>通过租户隔离 Registry 管理 Skill 定义、版本、发布状态及 Agent 兼容性声明。</p>
      </div>
      {error ? <div className="ops-query-error" role="alert"><span>{error}</span><button type="button" onClick={() => setError(null)}>关闭</button></div> : null}
      <div className="ops-dashboard-grid">
        <section className="ops-panel">
          <div className="ops-panel-heading"><h2>Skill Registry</h2><span>{items.length} 项 · 30 秒刷新</span></div>
          <label className="field-label" htmlFor="skills-filter">筛选 Skill</label>
          <input id="skills-filter" value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="按名称、ID、状态或定义筛选" />
          {listQuery.isLoading ? <p className="muted">正在加载 Skills…</p> : null}
          {listQuery.isError ? <div role="alert" className="ops-query-error"><span>{errorMessage(listQuery.error)}</span><button type="button" onClick={() => void listQuery.refetch()}>重试</button></div> : null}
          {!listQuery.isLoading && !listQuery.isError && filtered.length === 0 ? <p className="muted">{items.length ? '没有匹配的 Skill。' : '当前 Registry 暂无 Skill。'}</p> : null}
          <div className="ops-ranking" role="list">
            {filtered.map((item) => (
              <div role="listitem" key={item.item_id} className="ops-panel-heading">
                <div><strong>{labelFor(item)}</strong><small>{item.item_id} · v{item.version} · {item.status}</small></div>
                <Link className="text-link" to={`/admin/skills/${encodeURIComponent(item.item_id)}`}>详情</Link>
              </div>
            ))}
          </div>
          <hr />
          <h3>注册 Skill</h3>
          <label className="field-label" htmlFor="new-skill-id">Skill ID</label>
          <input id="new-skill-id" value={newId} onChange={(event) => setNewId(event.target.value)} placeholder="my-skill" />
          <label className="field-label" htmlFor="new-skill-definition">初始定义（JSON）</label>
          <textarea id="new-skill-definition" rows={7} value={newDefinition} onChange={(event) => setNewDefinition(event.target.value)} />
          <button type="button" disabled={busy || !newId.trim()} onClick={() => createDraft.mutate()}>创建草稿</button>
          <p className="muted">创建操作写入 Registry draft；发布前不会成为 active Skill。禁止在定义中写入明文密钥。</p>
        </section>
        <section className="ops-panel">
          <div className="ops-panel-heading"><h2>Skill 详情与生命周期</h2><span>{skillId ?? '未选择'}</span></div>
          {!skillId ? <p className="muted">选择 Skill 查看 active 定义、版本历史与生命周期操作。</p> : null}
          {skillId && activeQuery.isLoading ? <p className="muted">正在读取 active 版本…</p> : null}
          {skillId && activeQuery.isError ? <div role="alert" className="ops-query-error"><span>{errorMessage(activeQuery.error)}。该 Skill 可能尚无 active 版本。</span><button type="button" onClick={() => void activeQuery.refetch()}>重试</button></div> : null}
          {activeQuery.data ? <>
            <p><strong>{labelFor(activeQuery.data)}</strong></p>
            <p className="muted">active v{activeQuery.data.version} · {activeQuery.data.status}</p>
            <dl><dt>创建者</dt><dd>{activeQuery.data.created_by ?? '未提供'}</dd><dt>创建时间</dt><dd>{new Date(activeQuery.data.created_at).toLocaleString()}</dd><dt>发布时间</dt><dd>{activeQuery.data.published_at ? new Date(activeQuery.data.published_at).toLocaleString() : '未发布'}</dd><dt>使用该 Skill 的 Agent</dt><dd>{selected?.used_by?.join(', ') || 'Registry 未返回使用关系'}</dd></dl>
            <h3>Agent 兼容性</h3><pre className="code-preview">{compatibilityFor(activeQuery.data.definition)}</pre>
            <h3>Active 定义</h3><pre className="code-preview">{definitionText(activeQuery.data)}</pre>
            <button type="button" disabled={busy} onClick={() => disable.mutate()}>停用 Active 版本</button>
          </> : null}
          {skillId && !activeQuery.data && !activeQuery.isLoading && activeQuery.isError ? <p className="muted">可通过版本历史发布一个 draft 版本。</p> : null}
          {skillId ? <>
            <h3>创建新版本草稿</h3>
            <textarea aria-label="Skill 新版本定义 JSON" rows={8} value={editDefinition || definitionText(activeQuery.data ?? selected)} onChange={(event) => setEditDefinition(event.target.value)} />
            <button type="button" disabled={busy || !editDefinition.trim()} onClick={() => saveDraft.mutate()}>保存新版本草稿</button>
            <h3>版本历史</h3>
            {versionsQuery.isLoading ? <p className="muted">正在读取版本…</p> : null}
            {versionsQuery.isError ? <div role="alert" className="ops-query-error"><span>{errorMessage(versionsQuery.error)}</span><button type="button" onClick={() => void versionsQuery.refetch()}>重试</button></div> : null}
            {(versionsQuery.data?.items ?? []).slice().sort((a, b) => b.version - a.version).map((version) => (
              <div className="ops-panel-heading" key={version.version}>
                <div><strong>v{version.version} · {version.status}</strong><small>{new Date(version.created_at).toLocaleString()}</small></div>
                <div>
                  {version.status === 'draft' ? <button type="button" disabled={busy} onClick={() => publish.mutate(version.version)}>发布</button> : null}
                  {version.status === 'published' && activeQuery.data?.version !== version.version ? <button type="button" disabled={busy} onClick={() => rollback.mutate(version.version)}>回滚至此版本</button> : null}
                </div>
              </div>
            ))}
            <Link className="text-link" to="/admin/skills">返回 Skills 列表</Link>
          </> : null}
        </section>
      </div>
    </section>
  )
}
