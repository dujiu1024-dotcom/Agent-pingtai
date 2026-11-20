import { useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import './prompt-registry.css'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, apiClient } from '@/lib/api'
import { safePromptId } from './prompt-registry-utils'

type PromptSummary = {
  prompt_id: string
  latest_version: number
  active_version: number | null
  version_count: number
}

type PromptList = { items: PromptSummary[]; count: number }
type PromptVersionSummary = {
  prompt_id: string
  version: number
  variables: string[]
  content_hash: string
}
type PromptVersions = { items: PromptVersionSummary[]; count: number }
type PromptVersion = PromptVersionSummary & { template: string }
type PromptDiff = {
  prompt_id: string
  base_version: number
  version: number
  diff: string
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return '当前会话未通过身份认证，请重新登录。'
    if (error.status === 403) return '当前用户没有该 Prompt 的读取或管理权限。'
    if (error.status === 404) return 'Prompt 或指定版本不存在。'
    if (error.status === 409) return '评测 Gate 未通过，版本不能激活。'
    if (error.status === 503) return 'Prompt Registry 或评测 Gate 当前未配置。'
    return error.message
  }
  return error instanceof Error ? error.message : 'Prompt Registry 请求失败'
}

export function PromptRegistryPage({
  evaluationGateEnabled,
}: {
  evaluationGateEnabled: boolean
}) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { promptId } = useParams<{ promptId: string }>()
  const [filter, setFilter] = useState('')
  const [newPromptId, setNewPromptId] = useState('')
  const [template, setTemplate] = useState('你是一个专业助手，请根据以下信息完成任务：\n\n{input}')
  const [selectedVersion, setSelectedVersion] = useState<number | ''>('')
  const [baseVersion, setBaseVersion] = useState<number | ''>('')
  const [formError, setFormError] = useState<string | null>(null)

  const listQuery = useQuery({
    queryKey: ['prompt-registry'],
    queryFn: () => apiClient.get<PromptList>('/platform/v1/prompts/?limit=100'),
    staleTime: 10_000,
    refetchInterval: 30_000,
  })
  const prompts = listQuery.data?.items ?? []
  const filteredPrompts = useMemo(() => {
    const needle = filter.trim().toLocaleLowerCase()
    return needle
      ? prompts.filter((item) => item.prompt_id.toLocaleLowerCase().includes(needle))
      : prompts
  }, [filter, prompts])

  const summary = prompts.find((item) => item.prompt_id === promptId)
  const versionListQuery = useQuery({
    queryKey: ['prompt-versions', promptId],
    queryFn: () => apiClient.get<PromptVersions>(
      `/platform/v1/prompts/${encodeURIComponent(promptId!)}/versions`,
    ),
    enabled: Boolean(promptId),
  })
  const versions = versionListQuery.data?.items ?? []
  const currentVersion = selectedVersion || summary?.active_version || summary?.latest_version
  const detailQuery = useQuery({
    queryKey: ['prompt-version', promptId, currentVersion],
    queryFn: () => apiClient.get<PromptVersion>(
      `/platform/v1/prompts/${encodeURIComponent(promptId!)}/versions/${currentVersion}`,
    ),
    enabled: Boolean(promptId && currentVersion),
  })
  const diffQuery = useQuery({
    queryKey: ['prompt-diff', promptId, selectedVersion, baseVersion],
    queryFn: () => apiClient.get<PromptDiff>(
      `/platform/v1/prompts/${encodeURIComponent(promptId!)}` +
        `/versions/${selectedVersion}/diff?base_version=${baseVersion}`,
    ),
    enabled: Boolean(promptId && selectedVersion && baseVersion && selectedVersion !== baseVersion),
  })

  const createVersion = useMutation({
    mutationFn: ({ id, value }: { id: string; value: string }) =>
      apiClient.post<PromptVersionSummary>(
        `/platform/v1/prompts/${encodeURIComponent(id)}/versions`,
        { template: value },
      ),
    onSuccess: async (created, variables) => {
      await queryClient.invalidateQueries({ queryKey: ['prompt-registry'] })
      await queryClient.invalidateQueries({ queryKey: ['prompt-versions', variables.id] })
      setNewPromptId('')
      setTemplate('')
      setFormError(null)
      navigate(`/admin/prompts/${encodeURIComponent(variables.id)}`)
      setSelectedVersion(created.version)
    },
    onError: (error) => setFormError(errorMessage(error)),
  })
  const activateVersion = useMutation({
    mutationFn: (version: number) =>
      apiClient.post(
        `/platform/v1/prompts/${encodeURIComponent(promptId!)}/activate`,
        { version },
      ),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['prompt-registry'] }),
        queryClient.invalidateQueries({ queryKey: ['prompt-versions', promptId] }),
      ])
    },
  })

  const handleCreate = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const id = newPromptId.trim()
    if (!safePromptId(id)) {
      setFormError('Prompt ID 必须为 1-128 位字母、数字、点、下划线、冒号或连字符。')
      return
    }
    if (!template.trim()) {
      setFormError('Prompt 模板不能为空。')
      return
    }
    setFormError(null)
    createVersion.mutate({ id, value: template })
  }

  return (
    <section className="page-content prompt-registry-page">
      <div className="page-heading">
        <p className="eyebrow">PLATFORM MANAGEMENT / PROMPT REGISTRY</p>
        <h1>Prompt 与模板管理</h1>
        <p>按租户隔离的 Prompt 模板、不可变版本、差异追踪与评测门禁。激活、灰度和回滚必须由后端评测 Gate 授权。</p>
      </div>
      <div className="prompt-registry-layout">
        <section className="ops-panel prompt-list-panel">
          <div className="ops-panel-heading">
            <h2>Prompt 列表</h2>
            <span>{listQuery.data?.count ?? 0} 项 · 30 秒刷新</span>
          </div>
          <label className="field-label" htmlFor="prompt-filter">筛选 Prompt ID</label>
          <input
            id="prompt-filter"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="输入 Prompt ID"
          />
          {listQuery.isLoading ? <p className="muted">正在加载 Prompt…</p> : null}
          {listQuery.isError ? (
            <div className="ops-query-error" role="alert">
              <span>{errorMessage(listQuery.error)}</span>
              <button type="button" onClick={() => void listQuery.refetch()}>重试</button>
            </div>
          ) : null}
          {!listQuery.isLoading && !listQuery.isError && filteredPrompts.length === 0 ? (
            <p className="muted">{prompts.length ? '没有匹配的 Prompt。' : '当前租户还没有 Prompt 模板。'}</p>
          ) : null}
          <div className="prompt-list" role="list">
            {filteredPrompts.map((item) => (
              <Link
                key={item.prompt_id}
                role="listitem"
                className={
                  item.prompt_id === promptId
                    ? 'prompt-list-item active'
                    : 'prompt-list-item'
                }
                to={`/admin/prompts/${encodeURIComponent(item.prompt_id)}`}
              >
                <strong>{item.prompt_id}</strong>
                <small>共 {item.version_count} 个版本 · 最新 v{item.latest_version}</small>
                <small>{item.active_version ? `当前激活 v${item.active_version}` : '尚无激活版本'}</small>
              </Link>
            ))}
          </div>
        </section>

        <section className="ops-panel prompt-detail-panel">
          {promptId ? (
            <>
              <div className="ops-panel-heading">
                <div><h2>{promptId}</h2><span>Prompt 版本管理</span></div>
                <Link className="text-link" to="/admin/prompts">返回列表</Link>
              </div>
              {versionListQuery.isLoading ? <p className="muted">正在加载版本历史…</p> : null}
              {versionListQuery.isError ? (
                <p role="alert">{errorMessage(versionListQuery.error)}</p>
              ) : null}
              <div className="prompt-version-toolbar">
                <label className="field-label" htmlFor="prompt-version-select">查看版本</label>
                <select
                  id="prompt-version-select"
                  value={currentVersion ?? ''}
                  onChange={(event) =>
                    setSelectedVersion(
                      event.target.value ? Number(event.target.value) : '',
                    )
                  }
                >
                  {versions.map((version) => (
                    <option key={version.version} value={version.version}>
                      v{version.version}
                    </option>
                  ))}
                </select>
                <span className="muted">
                  {summary?.active_version
                    ? `Active v${summary.active_version}`
                    : '无 Active 版本'}
                </span>
              </div>
              {detailQuery.isLoading ? <p className="muted">正在读取模板内容…</p> : null}
              {detailQuery.isError ? <p role="alert">{errorMessage(detailQuery.error)}</p> : null}
              {detailQuery.data ? (
                <>
                  <div className="prompt-version-meta">
                    <span>v{detailQuery.data.version}</span>
                    <span>
                      变量：
                      {detailQuery.data.variables.length
                        ? detailQuery.data.variables.join(', ')
                        : '无'}
                    </span>
                    <span>SHA-256：{detailQuery.data.content_hash}</span>
                  </div>
                  <pre className="prompt-template-preview">{detailQuery.data.template}</pre>
                  <button
                    type="button"
                    disabled={
                      !evaluationGateEnabled ||
                      activateVersion.isPending ||
                      summary?.active_version === detailQuery.data.version
                    }
                    onClick={() => activateVersion.mutate(detailQuery.data!.version)}
                  >
                    {activateVersion.isPending ? '提交评测 Gate…' : '通过评测 Gate 并激活'}
                  </button>
                  {!evaluationGateEnabled ? (
                    <p className="muted">
                      当前后端未配置 Prompt Evaluation Gate，激活/灰度/回滚操作已禁用。
                    </p>
                  ) : null}
                  {activateVersion.isError ? (
                    <p role="alert">{errorMessage(activateVersion.error)}</p>
                  ) : null}
                </>
              ) : null}
              <h3>版本差异</h3>
              <div className="prompt-version-toolbar">
                <label htmlFor="prompt-diff-base">基准版本</label>
                <select
                  id="prompt-diff-base"
                  value={baseVersion}
                  onChange={(event) =>
                    setBaseVersion(
                      event.target.value ? Number(event.target.value) : '',
                    )
                  }
                >
                  <option value="">选择版本</option>
                  {versions.map((version) => (
                    <option key={version.version} value={version.version}>
                      v{version.version}
                    </option>
                  ))}
                </select>
                <label htmlFor="prompt-diff-target">目标版本</label>
                <select
                  id="prompt-diff-target"
                  value={selectedVersion}
                  onChange={(event) =>
                    setSelectedVersion(
                      event.target.value ? Number(event.target.value) : '',
                    )
                  }
                >
                  <option value="">选择版本</option>
                  {versions.map((version) => (
                    <option key={version.version} value={version.version}>
                      v{version.version}
                    </option>
                  ))}
                </select>
              </div>
              {diffQuery.isError ? <p role="alert">{errorMessage(diffQuery.error)}</p> : null}
              {diffQuery.data ? (
                <pre className="prompt-template-preview">
                  {diffQuery.data.diff || '两个版本没有文本差异。'}
                </pre>
              ) : null}
            </>
          ) : (
            <>
              <div className="ops-panel-heading">
                <h2>新建 Prompt 版本</h2>
                <span>未激活</span>
              </div>
              <p className="muted">填写新 Prompt ID 创建首个版本，或输入已有 ID 追加不可变版本。保存不会自动激活。</p>
              <form onSubmit={handleCreate} className="prompt-create-form">
                <label className="field-label" htmlFor="new-prompt-id">Prompt ID</label>
                <input
                  id="new-prompt-id"
                  value={newPromptId}
                  onChange={(event) => setNewPromptId(event.target.value)}
                  placeholder="例如：support.reply"
                  required
                />
                <label className="field-label" htmlFor="new-prompt-template">模板内容</label>
                <textarea
                  id="new-prompt-template"
                  rows={14}
                  value={template}
                  onChange={(event) => setTemplate(event.target.value)}
                  spellCheck={false}
                  required
                />
                <p className="muted">变量使用简单占位符，例如 {'{input}'}。保存后生成新版本，只有通过评测 Gate 才能激活。</p>
                {formError ? <p role="alert">{formError}</p> : null}
                <button type="submit" disabled={createVersion.isPending}>
                  {createVersion.isPending ? '保存中…' : '保存 Prompt 版本'}
                </button>
              </form>
            </>
          )}
        </section>
      </div>
    </section>
  )
}
