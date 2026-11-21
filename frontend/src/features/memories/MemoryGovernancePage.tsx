import { useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { EmptyState, ErrorState, LoadingState } from '@/components/States'
import { ApiError, apiClient } from '@/lib/api'
import { formatDateTime, localizedJson, valueLabel } from '@/lib/i18n/labels'
import './MemoryGovernancePage.css'

type MemoryCandidate = {
  memory_id: string
  scope: string
  scope_id: string
  content: string
  source_ref?: string
  confidence?: number
  conflict_key?: string | null
  status: string
  version?: number
}

type MemoryRecord = MemoryCandidate & {
  expires_at?: string | null
}

type MemoryExportItem = {
  memory_id?: string
  scope?: string
  scope_id?: string
  content?: string
  status?: string
  [key: string]: unknown
}

type ConflictGroup = {
  key: string
  conflictKey: string | null
  items: MemoryCandidate[]
}

type Tab = 'candidates' | 'conflicts' | 'search' | 'export'

const SCOPES = [
  ['', '全部范围'],
  ['user', '用户'],
  ['project', '项目'],
  ['agent', 'Agent'],
  ['tenant', '租户'],
] as const

const STATUS_LABELS: Record<string, string> = {
  candidate: '待确认',
  conflict: '冲突',
  active: '生效',
  archived: '已归档',
  superseded: '已取代',
  expired: '已过期',
}

const statusLabel = (value: string) => STATUS_LABELS[value] ?? valueLabel(value)

const confidenceText = (value?: number) =>
  typeof value === 'number' && Number.isFinite(value) ? value.toFixed(2) : '—'

const errorText = (error: unknown) => {
  if (error instanceof ApiError) {
    if (error.status === 401) return '登录状态已失效，请重新登录。'
    if (error.status === 403) return '当前账号没有该记忆范围的管理权限。'
    if (error.status === 404) return '记忆记录不存在或已被删除。'
    if (error.status === 409) return '记忆状态已变化，请刷新后重试。'
    if (error.status === 422) return '请求参数不符合服务端要求，请检查输入。'
    if (error.status === 503) return '记忆服务暂不可用，请稍后重试。'
  }
  return error instanceof Error ? error.message : '请求失败，请稍后重试。'
}

const downloadJson = (filename: string, payload: unknown) => {
  const blob = new Blob([JSON.stringify(payload, null, 2)], {
    type: 'application/json;charset=utf-8',
  })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

export function MemoryGovernancePage() {
  const queryClient = useQueryClient()
  const [tab, setTab] = useState<Tab>('candidates')
  const [actionError, setActionError] = useState('')
  const [notice, setNotice] = useState('')
  const [selections, setSelections] = useState<Record<string, string>>({})
  const [searchKeyword, setSearchKeyword] = useState('')
  const [searchScope, setSearchScope] = useState('')
  const [submittedSearch, setSubmittedSearch] = useState({ keyword: '', scope: '' })
  const [exportScope, setExportScope] = useState('')

  const candidatesQuery = useQuery({
    queryKey: ['memory-governance', 'candidates'],
    queryFn: () =>
      apiClient.get<{ items: MemoryCandidate[] }>('/platform/v1/memories/candidates'),
    retry: false,
  })

  const activeQuery = useQuery({
    queryKey: ['memory-governance', 'active'],
    queryFn: () =>
      apiClient.get<{ items: MemoryRecord[] }>('/platform/v1/memories?limit=200'),
    enabled: tab === 'search',
    retry: false,
  })

  const refreshAll = () =>
    queryClient.invalidateQueries({ queryKey: ['memory-governance'] })

  const confirmMutation = useMutation({
    mutationFn: (memoryId: string) =>
      apiClient.post<{ memory_id: string; status: string; version: number }>(
        `/platform/v1/memories/${encodeURIComponent(memoryId)}/confirm`,
      ),
    onMutate: () => {
      setActionError('')
      setNotice('')
    },
    onSuccess: async (data) => {
      setNotice(`已确认记忆，当前状态：${statusLabel(data.status)}。`)
      await refreshAll()
    },
    onError: (error) => setActionError(errorText(error)),
  })

  const rejectMutation = useMutation({
    mutationFn: (memoryId: string) =>
      apiClient.delete(`/platform/v1/memories/${encodeURIComponent(memoryId)}`),
    onMutate: () => {
      setActionError('')
      setNotice('')
    },
    onSuccess: async () => {
      setNotice('已拒绝并删除该记忆候选。')
      await refreshAll()
    },
    onError: (error) => setActionError(errorText(error)),
  })

  const archiveMutation = useMutation({
    mutationFn: (memoryId: string) =>
      apiClient.post<{ memory_id: string; status: string }>(
        `/platform/v1/memories/${encodeURIComponent(memoryId)}/archive`,
      ),
    onMutate: () => {
      setActionError('')
      setNotice('')
    },
    onSuccess: async () => {
      setNotice('记忆已归档。')
      await refreshAll()
    },
    onError: (error) => setActionError(errorText(error)),
  })

  const resolveMutation = useMutation({
    mutationFn: (payload: { conflict_key: string; selected_memory_id: string }) =>
      apiClient.post<{ memory_id: string; status: string; version: number }>(
        '/platform/v1/memories/conflicts/resolve',
        payload,
      ),
    onMutate: () => {
      setActionError('')
      setNotice('')
    },
    onSuccess: async (data) => {
      setNotice(`冲突已解决，选中记忆状态：${statusLabel(data.status)}。`)
      await refreshAll()
    },
    onError: (error) => setActionError(errorText(error)),
  })

  const exportMutation = useMutation({
    mutationFn: async (scope: string) => {
      const data = await apiClient.get<{ items?: MemoryExportItem[] }>(
        '/platform/v1/memories/export',
      )
      const all = data.items ?? []
      const items = scope ? all.filter((item) => item.scope === scope) : all
      const suffix = scope || 'all'
      downloadJson(`memories-${suffix}-${new Date().toISOString().slice(0, 10)}.json`, {
        items,
      })
      return items.length
    },
    onMutate: () => {
      setActionError('')
      setNotice('')
    },
    onSuccess: (count, scope) => {
      setNotice(
        `已导出 ${count} 条记忆记录${scope ? `（范围：${valueLabel(scope)}）` : '（全部可见范围）'}。`,
      )
    },
    onError: (error) => setActionError(errorText(error)),
  })

  const candidates = useMemo(
    () => candidatesQuery.data?.items ?? [],
    [candidatesQuery.data],
  )
  const pendingCandidates = useMemo(
    () => candidates.filter((item) => item.status === 'candidate'),
    [candidates],
  )
  const conflictGroups = useMemo<ConflictGroup[]>(() => {
    const groups = new Map<string, MemoryCandidate[]>()
    for (const item of candidates) {
      if (item.status !== 'conflict') continue
      const key = item.conflict_key || `single:${item.memory_id}`
      const group = groups.get(key)
      if (group) group.push(item)
      else groups.set(key, [item])
    }
    return [...groups.entries()].map(([key, items]) => ({
      key,
      conflictKey: items[0]?.conflict_key || null,
      items,
    }))
  }, [candidates])

  const activeItems = useMemo(() => {
    const keyword = submittedSearch.keyword.toLowerCase()
    return (activeQuery.data?.items ?? []).filter((item) => {
      if (submittedSearch.scope && item.scope !== submittedSearch.scope) return false
      if (!keyword) return true
      return [item.content, item.source_ref, item.scope_id, item.memory_id].some(
        (value) => (value ?? '').toLowerCase().includes(keyword),
      )
    })
  }, [activeQuery.data, submittedSearch])

  const submitSearch = (event: FormEvent) => {
    event.preventDefault()
    setSubmittedSearch({ keyword: searchKeyword.trim(), scope: searchScope })
  }

  const resolveGroup = (group: ConflictGroup) => {
    if (!group.conflictKey) {
      setActionError('该冲突缺少冲突键，服务端无法解析，请检查记忆生成结果。')
      return
    }
    const selected = selections[group.key] ?? group.items[0]?.memory_id
    if (!selected) {
      setActionError('请选择要保留的记忆记录。')
      return
    }
    resolveMutation.mutate({
      conflict_key: group.conflictKey,
      selected_memory_id: selected,
    })
  }

  const rejectCandidate = (item: MemoryCandidate) => {
    if (!window.confirm('确认拒绝并删除这条记忆候选？该操作不可恢复。')) return
    rejectMutation.mutate(item.memory_id)
  }

  return (
    <section className="page-content mg-page">
      <div className="page-heading mg-heading">
        <div>
          <p className="eyebrow">智能体运营 / 记忆治理</p>
          <h1>记忆治理</h1>
          <p>审核记忆候选、解决冲突、检索生效记忆并按范围导出；所有写入以服务端返回版本为准。</p>
        </div>
        <button
          className="button-secondary"
          type="button"
          onClick={() => void refreshAll()}
          disabled={candidatesQuery.isFetching}
        >
          {candidatesQuery.isFetching ? '刷新中…' : '刷新数据'}
        </button>
      </div>

      <nav className="panel-tabs" role="tablist" aria-label="记忆治理分区">
        {([
          ['candidates', '候选记忆', pendingCandidates.length],
          ['conflicts', '冲突', conflictGroups.length],
          ['search', '检索', activeItems.length],
          ['export', '导出', 0],
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
            {id !== 'export' && <span className="panel-tab-count">{count}</span>}
          </button>
        ))}
      </nav>

      {actionError && (
        <p className="mg-message mg-message--error" role="alert">
          {actionError}
        </p>
      )}
      {notice && (
        <p className="mg-message mg-message--success" role="status">
          {notice}
        </p>
      )}

      {tab === 'candidates' && (
        <section className="mg-panel">
          <header>
            <div>
              <h2>待确认记忆候选</h2>
              <p>确认后候选进入生效状态；拒绝将删除该候选。确认与拒绝均走服务端权限校验。</p>
            </div>
            <span>{pendingCandidates.length} 条</span>
          </header>
          {candidatesQuery.isPending ? (
            <LoadingState label="正在读取记忆候选" />
          ) : candidatesQuery.isError ? (
            <ErrorState
              title="记忆候选读取失败"
              description={errorText(candidatesQuery.error)}
              onRetry={() => void candidatesQuery.refetch()}
            />
          ) : pendingCandidates.length === 0 ? (
            <EmptyState
              title="暂无待确认候选"
              description="当前账号可管理的范围内没有待确认的记忆候选。"
            />
          ) : (
            <div className="mg-table-wrap">
              <table className="mg-table">
                <thead>
                  <tr>
                    <th>内容</th>
                    <th>范围</th>
                    <th>范围标识</th>
                    <th>置信度</th>
                    <th>来源</th>
                    <th>冲突键</th>
                    <th>版本</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {pendingCandidates.map((item) => (
                    <tr key={item.memory_id}>
                      <td className="mg-content">{item.content || '—'}</td>
                      <td>{valueLabel(item.scope)}</td>
                      <td>{item.scope_id || '—'}</td>
                      <td>{confidenceText(item.confidence)}</td>
                      <td className="mg-source">{item.source_ref || '—'}</td>
                      <td>{item.conflict_key || '—'}</td>
                      <td>{item.version ?? '—'}</td>
                      <td>
                        <div className="mg-actions">
                          <button
                            className="button-primary"
                            type="button"
                            disabled={confirmMutation.isPending || rejectMutation.isPending}
                            onClick={() => confirmMutation.mutate(item.memory_id)}
                          >
                            确认
                          </button>
                          <button
                            className="button-secondary"
                            type="button"
                            disabled={confirmMutation.isPending || rejectMutation.isPending}
                            onClick={() => rejectCandidate(item)}
                          >
                            拒绝
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {tab === 'conflicts' && (
        <section className="mg-panel">
          <header>
            <div>
              <h2>记忆冲突</h2>
              <p>同一冲突键下的候选需要选定保留记录；解决接口只接受冲突键与选中记忆 ID。</p>
            </div>
            <span>{conflictGroups.length} 组</span>
          </header>
          {candidatesQuery.isPending ? (
            <LoadingState label="正在读取冲突记录" />
          ) : candidatesQuery.isError ? (
            <ErrorState
              title="冲突记录读取失败"
              description={errorText(candidatesQuery.error)}
              onRetry={() => void candidatesQuery.refetch()}
            />
          ) : conflictGroups.length === 0 ? (
            <EmptyState
              title="暂无记忆冲突"
              description="当前没有需要人工裁决的冲突候选；新候选写入后会出现在这里。"
            />
          ) : (
            <div className="mg-conflict-list">
              {conflictGroups.map((group) => (
                <article className="mg-conflict-card" key={group.key}>
                  <header>
                    <div>
                      <h3>冲突键：{group.conflictKey || '缺失'}</h3>
                      <p>{group.items.length} 条候选记录</p>
                    </div>
                    <button
                      className="button-primary"
                      type="button"
                      disabled={
                        !group.conflictKey || resolveMutation.isPending
                      }
                      onClick={() => resolveGroup(group)}
                    >
                      解决冲突
                    </button>
                  </header>
                  {!group.conflictKey && (
                    <p className="mg-inline-warning">
                      该组记录缺少 conflict_key，无法调用解决接口，请检查记忆生成来源。
                    </p>
                  )}
                  <ul className="mg-candidates">
                    {group.items.map((item) => (
                      <li key={item.memory_id}>
                        <label>
                          <input
                            type="radio"
                            name={`conflict-${group.key}`}
                            checked={
                              (selections[group.key] ?? group.items[0]?.memory_id) ===
                              item.memory_id
                            }
                            onChange={() =>
                              setSelections((previous) => ({
                                ...previous,
                                [group.key]: item.memory_id,
                              }))
                            }
                          />
                          <span className="mg-candidate-copy">
                            <strong>{item.content || '—'}</strong>
                            <small>
                              {valueLabel(item.scope)} · {item.scope_id || '—'} · 置信度{' '}
                              {confidenceText(item.confidence)} · 来源 {item.source_ref || '—'}
                            </small>
                          </span>
                        </label>
                      </li>
                    ))}
                  </ul>
                  <p className="mg-hint">记忆 ID：{group.items.map((item) => item.memory_id).join(' / ')}</p>
                  <details className="mg-details">
                    <summary>查看原始记录</summary>
                    <pre>{localizedJson(group.items)}</pre>
                  </details>
                </article>
              ))}
            </div>
          )}
        </section>
      )}

      {tab === 'search' && (
        <section className="mg-panel">
          <header>
            <div>
              <h2>检索生效记忆</h2>
              <p>服务端返回当前账号可见的生效记忆；关键词与范围在当前结果内筛选。</p>
            </div>
            <span>{activeItems.length} 条</span>
          </header>
          <form className="mg-filters" onSubmit={submitSearch}>
            <label>
              关键词
              <input
                value={searchKeyword}
                onChange={(event) => setSearchKeyword(event.target.value)}
                placeholder="内容、来源、范围标识或记忆 ID"
                maxLength={200}
              />
            </label>
            <label>
              范围
              <select
                value={searchScope}
                onChange={(event) => setSearchScope(event.target.value)}
              >
                {SCOPES.map(([value, label]) => (
                  <option key={value || 'all'} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <button className="button-primary" type="submit">
              检索
            </button>
          </form>
          {activeQuery.isPending ? (
            <LoadingState label="正在读取生效记忆" />
          ) : activeQuery.isError ? (
            <ErrorState
              title="记忆检索失败"
              description={errorText(activeQuery.error)}
              onRetry={() => void activeQuery.refetch()}
            />
          ) : activeItems.length === 0 ? (
            <EmptyState
              title="没有匹配的记忆"
              description="当前可见范围内没有生效记忆，或没有记录命中关键词与范围筛选。"
            />
          ) : (
            <div className="mg-table-wrap">
              <table className="mg-table">
                <thead>
                  <tr>
                    <th>内容</th>
                    <th>范围</th>
                    <th>范围标识</th>
                    <th>置信度</th>
                    <th>状态</th>
                    <th>过期时间</th>
                    <th>来源</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {activeItems.map((item) => (
                    <tr key={item.memory_id}>
                      <td className="mg-content">{item.content || '—'}</td>
                      <td>{valueLabel(item.scope)}</td>
                      <td>{item.scope_id || '—'}</td>
                      <td>{confidenceText(item.confidence)}</td>
                      <td>{statusLabel(item.status)}</td>
                      <td>{item.expires_at ? formatDateTime(item.expires_at) : '—'}</td>
                      <td className="mg-source">{item.source_ref || '—'}</td>
                      <td>
                        <button
                          className="button-secondary"
                          type="button"
                          disabled={archiveMutation.isPending}
                          onClick={() => archiveMutation.mutate(item.memory_id)}
                        >
                          归档
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {tab === 'export' && (
        <section className="mg-panel">
          <header>
            <div>
              <h2>导出记忆</h2>
              <p>
                服务端导出接口返回当前账号可管理的全部记忆（含历史版本）；范围筛选在导出结果内执行。
              </p>
            </div>
          </header>
          <div className="mg-export">
            <label>
              导出范围
              <select
                value={exportScope}
                onChange={(event) => setExportScope(event.target.value)}
              >
                {SCOPES.map(([value, label]) => (
                  <option key={value || 'all'} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="button-primary"
              type="button"
              disabled={exportMutation.isPending}
              onClick={() => exportMutation.mutate(exportScope)}
            >
              {exportMutation.isPending ? '导出中…' : '导出 JSON'}
            </button>
          </div>
          <p className="mg-hint">
            下载内容与 GET /platform/v1/memories/export 返回结构一致（items 数组），不会在前端补造字段。
          </p>
        </section>
      )}
    </section>
  )
}
