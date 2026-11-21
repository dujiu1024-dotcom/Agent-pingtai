import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { EmptyState, ErrorState, LoadingState } from '@/components/States'
import { Pagination } from '@/components/Pagination'
import { ApiError, apiClient } from '@/lib/api'
import { formatDateTime, localizedJson, valueLabel } from '@/lib/i18n/labels'
import './PromptLifecyclePage.css'

const PROMPT_LABELS: Record<string, string> = {
  'finance-agent-system-prompt': '财务专员 · System Prompt',
  'ops-agent-system-prompt': '运维专员 · System Prompt',
  'hr-agent-system-prompt': 'HR 专员 · System Prompt',
  'task-dispatch-prompt': '任务交办 · Prompt 模版',
  'finance-report-rendering': '财务汇报 · 渲染模版',
  'tenant:3:fde:smoke': '冒烟测试（可忽略）',
}

const promptLabel = (promptId: string) =>
  PROMPT_LABELS[promptId] ?? promptId

type PromptRow = {
  prompt_id: string
  latest_version?: number | null
  active_version?: number | null
  total_versions?: number | null
}

type VersionRow = {
  prompt_id: string
  version: number
  variables?: string[]
  content_hash?: string
}

type PromptAuditRow = {
  event_id?: string | number
  prompt_id?: string
  action?: string
  actor_id?: string
  details?: unknown
  created_at?: string
}

type RenderedPrompt = {
  prompt_id: string
  version: number
  content_hash: string
  rendered: string
}

const ACTION_LABELS: Record<string, string> = {
  'version.registered': '注册新版本',
  'version.activated': '激活版本',
  'version.rolled_back': '回滚版本',
  'canary.configured': '配置灰度',
  'variant.created': '创建变体',
  'variant.version_assigned': '更新变体版本',
  'variant.deprecated': '弃用变体',
  'prompt.version.tested': '测试版本',
  'prompt.version.release_requested': '发布请求',
  'prompt.version.rollback_requested': '回滚请求',
}

const actionLabel = (action: unknown) =>
  typeof action === 'string'
    ? (ACTION_LABELS[action] ?? valueLabel(action))
    : valueLabel(action)

const errorText = (error: unknown) => {
  if (error instanceof ApiError) {
    if (error.status === 403) return '当前账号没有 Prompt 管理权限。'
    if (error.status === 404) return '提示词或版本不存在，请刷新后重试。'
    if (error.status === 409)
      return '操作被拒绝：候选版本未通过评测门禁，或当前状态不满足发布 / 灰度条件。'
    if (error.status === 503)
      return '服务端未配置评测门禁或变更审计，该操作暂不可用。'
    if (error.status === 400) return '请求参数不合法，请检查版本号与模板内容。'
  }
  return error instanceof Error && error.message
    ? error.message
    : '请求失败，请稍后重试。'
}

const shortHash = (value: unknown) =>
  typeof value === 'string' && value ? `${value.slice(0, 12)}…` : '—'

const effectiveVersion = (value: string, versions: VersionRow[]): string => {
  if (value && versions.some((item) => String(item.version) === value)) {
    return value
  }
  return versions.length ? String(versions[versions.length - 1].version) : ''
}

export function PromptLifecyclePage() {
  const queryClient = useQueryClient()
  const [selectedPromptId, setSelectedPromptId] = useState('')
  const [activeTab, setActiveTab] = useState<'versions' | 'release' | 'render' | 'audit'>(
    'versions',
  )
  const [targetVersion, setTargetVersion] = useState('')
  const [canaryPercentage, setCanaryPercentage] = useState('10')
  const [newTemplate, setNewTemplate] = useState('')
  const [viewVersion, setViewVersion] = useState<number | null>(null)
  const [renderVersion, setRenderVersion] = useState('')
  const [rolloutKey, setRolloutKey] = useState('')
  const [renderVariables, setRenderVariables] = useState<
    Record<string, string>
  >({})
  const [renderResult, setRenderResult] = useState<RenderedPrompt | null>(null)
  const [renderError, setRenderError] = useState('')
  const [notice, setNotice] = useState('')
  const [actionError, setActionError] = useState('')
  const [auditPage, setAuditPage] = useState(1)

  useEffect(() => { setAuditPage(1) }, [selectedPromptId, activeTab])

  const promptsQuery = useQuery({
    queryKey: ['prompt-lifecycle', 'prompts'],
    queryFn: () =>
      apiClient.get<{ items?: PromptRow[] }>('/platform/v1/prompts/?limit=200'),
    retry: false,
  })

  const prompts = promptsQuery.data?.items ?? []
  const effectivePromptId = prompts.some(
    (item) => item.prompt_id === selectedPromptId,
  )
    ? selectedPromptId
    : (prompts[0]?.prompt_id ?? '')
  const selectedPrompt = prompts.find(
    (item) => item.prompt_id === effectivePromptId,
  )
  const promptBase = effectivePromptId
    ? `/platform/v1/prompts/${encodeURIComponent(effectivePromptId)}`
    : ''

  const versionsQuery = useQuery({
    queryKey: ['prompt-lifecycle', 'versions', effectivePromptId],
    queryFn: () =>
      apiClient.get<{ items?: VersionRow[] }>(`${promptBase}/versions`),
    enabled: Boolean(effectivePromptId),
    retry: false,
  })

  const versions = [...(versionsQuery.data?.items ?? [])].sort(
    (left, right) => left.version - right.version,
  )
  const target = effectiveVersion(targetVersion, versions)
  const renderTarget = effectiveVersion(renderVersion, versions)
  const renderVersionRow = versions.find(
    (item) => String(item.version) === renderTarget,
  )
  const renderVariableNames = renderVersionRow?.variables ?? []

  const auditQuery = useQuery({
    queryKey: ['prompt-lifecycle', 'audit', effectivePromptId],
    queryFn: () =>
      apiClient.get<{ items?: PromptAuditRow[] }>(
        `${promptBase}/audit?limit=100`,
      ),
    enabled: Boolean(effectivePromptId),
    retry: false,
  })

  const templateQuery = useQuery({
    queryKey: ['prompt-lifecycle', 'template', effectivePromptId, viewVersion],
    queryFn: () =>
      apiClient.get<{ template?: string }>(
        `${promptBase}/versions/${viewVersion}`,
      ),
    enabled: Boolean(effectivePromptId) && viewVersion !== null,
    retry: false,
  })

  const invalidateAll = () =>
    queryClient.invalidateQueries({ queryKey: ['prompt-lifecycle'] })

  const operation = useMutation({
    mutationFn: (task: { label: string; run: () => Promise<unknown> }) =>
      task.run(),
    onSuccess: async (_result, task) => {
      setActionError('')
      setNotice(`${task.label}已完成。`)
      await invalidateAll()
    },
    onError: (error) => {
      setNotice('')
      setActionError(errorText(error))
    },
  })

  const createVersion = useMutation({
    mutationFn: () =>
      apiClient.post<{ version?: number }>(`${promptBase}/versions`, {
        template: newTemplate,
      }),
    onSuccess: async (data) => {
      setActionError('')
      setNotice(
        typeof data?.version === 'number'
          ? `已注册新版本 v${data.version}。`
          : '已注册新版本。',
      )
      setNewTemplate('')
      await invalidateAll()
    },
    onError: (error) => {
      setNotice('')
      setActionError(errorText(error))
    },
  })

  const renderPreview = useMutation({
    mutationFn: () =>
      apiClient.post<RenderedPrompt>(`${promptBase}/render`, {
        variables: Object.fromEntries(
          renderVariableNames.map((name) => [
            name,
            renderVariables[name] ?? '',
          ]),
        ),
        rollout_key: rolloutKey.trim(),
        version: Number(renderTarget),
      }),
    onSuccess: (data) => {
      setRenderError('')
      setRenderResult(data)
    },
    onError: (error) => {
      setRenderResult(null)
      setRenderError(errorText(error))
    },
  })

  const changePrompt = (next: string) => {
    setSelectedPromptId(next)
    setTargetVersion('')
    setRenderVersion('')
    setViewVersion(null)
    setRenderVariables({})
    setRenderResult(null)
    setRenderError('')
    setNotice('')
    setActionError('')
  }

  const refreshAll = () => {
    void promptsQuery.refetch()
    void versionsQuery.refetch()
    void auditQuery.refetch()
  }

  const publish = () => {
    if (!target) return
    if (!window.confirm(`确认将 v${target} 发布为当前生效版本？`)) return
    operation.mutate({
      label: `发布 v${target}`,
      run: () =>
        apiClient.post(`${promptBase}/activate`, { version: Number(target) }),
    })
  }

  const configureCanary = () => {
    if (!target) return
    const percentage = Number(canaryPercentage)
    if (!Number.isFinite(percentage) || percentage <= 0 || percentage > 100) {
      setActionError('灰度比例需要大于 0 且不超过 100。')
      return
    }
    if (!window.confirm(`确认将 v${target} 以 ${percentage}% 比例灰度发布？`)) {
      return
    }
    operation.mutate({
      label: `灰度 v${target}`,
      run: () =>
        apiClient.post(`${promptBase}/canary`, {
          version: Number(target),
          percentage,
        }),
    })
  }

  const rollback = () => {
    if (!target) return
    if (!window.confirm(`确认回滚到 v${target}？当前生效版本将被替换。`)) return
    operation.mutate({
      label: `回滚到 v${target}`,
      run: () =>
        apiClient.post(`${promptBase}/rollback`, { version: Number(target) }),
    })
  }

  const refreshing =
    promptsQuery.isFetching || versionsQuery.isFetching || auditQuery.isFetching
  const auditRows = auditQuery.data?.items ?? []

  return (
    <section className="page-content prompt-lifecycle">
      <div className="page-heading">
        <div>
          <p className="eyebrow">平台管理 / Prompt 生命周期</p>
          <h1>Prompt 生命周期</h1>
          <p>
            管理提示词版本、发布与灰度，查看变更审计，并用实际变量渲染预览。
            所有写操作都经服务端评测门禁与审计校验。
          </p>
        </div>
        <button
          type="button"
          className="button-secondary"
          onClick={refreshAll}
          disabled={refreshing}
        >
          {refreshing ? '刷新中…' : '刷新数据'}
        </button>
      </div>

      {notice && (
        <p className="prompt-notice" role="status">
          {notice}
        </p>
      )}
      {actionError && (
        <p className="prompt-error" role="alert">
          {actionError}
        </p>
      )}

      <section className="prompt-card">
        <header className="prompt-card-head">
          <div>
            <h2>选择提示词</h2>
            <p>提示词按服务端已注册的版本分组，选择后加载版本与审计记录。</p>
          </div>
        </header>

        {promptsQuery.isPending && <LoadingState label="正在加载提示词列表" />}

        {promptsQuery.isError && (
          <ErrorState
            title="提示词列表加载失败"
            description={errorText(promptsQuery.error)}
            onRetry={() => void promptsQuery.refetch()}
          />
        )}

        {promptsQuery.isSuccess && prompts.length === 0 && (
          <EmptyState
            title="暂无提示词"
            description="服务端还没有注册任何提示词版本。请先通过创建版本或后端导入登记。"
          />
        )}

        {promptsQuery.isSuccess && prompts.length > 0 && (
          <div className="prompt-selector">
            <label>
              提示词
              <select
                value={effectivePromptId}
                onChange={(event) => changePrompt(event.target.value)}
              >
                {prompts.map((item) => (
                  <option key={item.prompt_id} value={item.prompt_id}>
                    {promptLabel(item.prompt_id)}
                  </option>
                ))}
              </select>
            </label>
            {selectedPrompt && (
              <p className="prompt-meta">
                最新版本{' '}
                {selectedPrompt.latest_version != null
                  ? `v${selectedPrompt.latest_version}`
                  : '—'}
                {' · '}生效版本{' '}
                {selectedPrompt.active_version != null
                  ? `v${selectedPrompt.active_version}`
                  : '未发布'}
                {' · '}共 {selectedPrompt.total_versions ?? versions.length}{' '}
                个版本
              </p>
            )}
          </div>
        )}
      </section>

      {effectivePromptId && (
        <>
          <ul className="prompt-tabs" role="tablist">
            <li>
              <button
                type="button"
                className="prompt-tab"
                data-active={activeTab === 'versions'}
                onClick={() => setActiveTab('versions')}
                role="tab"
              >
                版本管理
              </button>
            </li>
            <li>
              <button
                type="button"
                className="prompt-tab"
                data-active={activeTab === 'release'}
                onClick={() => setActiveTab('release')}
                role="tab"
              >
                发布与灰度
              </button>
            </li>
            <li>
              <button
                type="button"
                className="prompt-tab"
                data-active={activeTab === 'render'}
                onClick={() => setActiveTab('render')}
                role="tab"
              >
                渲染预览
              </button>
            </li>
            <li>
              <button
                type="button"
                className="prompt-tab"
                data-active={activeTab === 'audit'}
                onClick={() => setActiveTab('audit')}
                role="tab"
              >
                变更审计
              </button>
            </li>
          </ul>

          {activeTab === 'versions' && (
            <div className="prompt-tab-panel">
              <section className="prompt-card">
                <header className="prompt-card-head">
                  <div>
                    <h2>版本列表</h2>
                    <p>版本内容不可变；可查看模板后再决定发布、灰度或回滚。</p>
                  </div>
                  <span className="prompt-count">{versions.length} 个版本</span>
                </header>

                {versionsQuery.isPending && <LoadingState label="正在加载版本" />}

                {versionsQuery.isError && (
                  <ErrorState
                    title="版本列表加载失败"
                    description={errorText(versionsQuery.error)}
                    onRetry={() => void versionsQuery.refetch()}
                  />
                )}

                {versionsQuery.isSuccess && versions.length === 0 && (
                  <EmptyState
                    title="暂无版本"
                    description="该提示词还没有可用的版本记录，请先创建新版本。"
                  />
                )}

                {versions.length > 0 && (
                  <div className="prompt-table-wrap">
                    <table className="prompt-table">
                      <thead>
                        <tr>
                          <th>版本</th>
                          <th>变量</th>
                          <th>内容哈希</th>
                          <th>操作</th>
                        </tr>
                      </thead>
                      <tbody>
                        {versions.map((item) => (
                          <tr key={`${item.prompt_id}-${item.version}`}>
                            <td className="prompt-nowrap">v{item.version}</td>
                            <td>
                              {item.variables && item.variables.length > 0
                                ? item.variables.join('、')
                                : '无变量'}
                            </td>
                            <td className="prompt-nowrap" title={item.content_hash}>
                              {shortHash(item.content_hash)}
                            </td>
                            <td className="prompt-nowrap">
                              <button
                                className="text-link"
                                type="button"
                                onClick={() =>
                                  setViewVersion(
                                    viewVersion === item.version
                                      ? null
                                      : item.version,
                                  )
                                }
                              >
                                {viewVersion === item.version
                                  ? '收起模板'
                                  : '查看模板'}
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {viewVersion !== null && (
                  <div className="prompt-template">
                    <header>
                      <h3>v{viewVersion} 模板内容</h3>
                      <button
                        className="text-link"
                        type="button"
                        onClick={() => setViewVersion(null)}
                      >
                        收起
                      </button>
                    </header>
                    {templateQuery.isPending && (
                      <LoadingState label="正在加载模板" />
                    )}
                    {templateQuery.isError && (
                      <p className="prompt-error" role="alert">
                        {errorText(templateQuery.error)}
                      </p>
                    )}
                    {templateQuery.data && (
                      <pre>{templateQuery.data.template ?? '—'}</pre>
                    )}
                  </div>
                )}
              </section>

              <section className="prompt-card">
                <header className="prompt-card-head">
                  <div>
                    <h2>创建新版本</h2>
                    <p>提交模板后由服务端分配新版本号并记录变更审计。</p>
                  </div>
                </header>
                <label className="prompt-textarea">
                  模板内容
                  <textarea
                    rows={10}
                    value={newTemplate}
                    onChange={(event) => setNewTemplate(event.target.value)}
                    placeholder="使用 {{variable}} 占位符，例如：你是 {{role}}，请回答 {{question}}。"
                  />
                </label>
                <div className="prompt-actions">
                  <button
                    className="button-primary"
                    type="button"
                    onClick={() => createVersion.mutate()}
                    disabled={!newTemplate.trim() || createVersion.isPending}
                  >
                    {createVersion.isPending ? '提交中…' : '创建新版本'}
                  </button>
                </div>
              </section>
            </div>
          )}

          {activeTab === 'release' && (
            <section className="prompt-card">
              <header className="prompt-card-head">
                <div>
                  <h2>发布 / 灰度 / 回滚</h2>
                  <p>发布与回滚会切换生效版本；灰度按比例导入候选版本。</p>
                </div>
              </header>
              <div className="prompt-form-grid">
                <label>
                  目标版本
                  <select
                    value={target}
                    onChange={(event) => setTargetVersion(event.target.value)}
                    disabled={versions.length === 0}
                  >
                    {versions.map((item) => (
                      <option key={item.version} value={String(item.version)}>
                        v{item.version}
                        {selectedPrompt?.active_version === item.version
                          ? '（当前生效）'
                          : ''}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  灰度比例（%）
                  <input
                    type="number"
                    min={0.1}
                    max={100}
                    step={0.1}
                    value={canaryPercentage}
                    onChange={(event) => setCanaryPercentage(event.target.value)}
                  />
                </label>
              </div>
              <div className="prompt-actions">
                <button
                  className="button-primary"
                  type="button"
                  onClick={publish}
                  disabled={!target || operation.isPending}
                >
                  发布 / 激活
                </button>
                <button
                  className="button-secondary"
                  type="button"
                  onClick={configureCanary}
                  disabled={!target || operation.isPending}
                >
                  配置灰度
                </button>
                <button
                  className="button-secondary prompt-danger"
                  type="button"
                  onClick={rollback}
                  disabled={!target || operation.isPending}
                >
                  回滚到此版本
                </button>
              </div>
              <p className="prompt-hint">
                发布、灰度和回滚都需要服务端评测门禁通过；灰度比例需大于 0
                且不超过 100，候选版本不能与当前生效版本相同。
              </p>
            </section>
          )}

          {activeTab === 'render' && (
            <section className="prompt-card">
              <header className="prompt-card-head">
                <div>
                  <h2>渲染预览</h2>
                  <p>
                    按所选版本的变量定义填写值，调用服务端渲染接口查看实际输出。
                  </p>
                </div>
              </header>
              <div className="prompt-form-grid">
                <label>
                  渲染版本
                  <select
                    value={renderTarget}
                    onChange={(event) => setRenderVersion(event.target.value)}
                    disabled={versions.length === 0}
                  >
                    {versions.map((item) => (
                      <option key={item.version} value={String(item.version)}>
                        v{item.version}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  灰度键（rollout_key，可选）
                  <input
                    value={rolloutKey}
                    onChange={(event) => setRolloutKey(event.target.value)}
                    placeholder="用于稳定命中灰度分桶"
                  />
                </label>
              </div>

              {renderVariableNames.length === 0 ? (
                <p className="prompt-hint">该版本没有变量，可直接渲染预览。</p>
              ) : (
                <div className="prompt-vars">
                  {renderVariableNames.map((name) => (
                    <label key={name}>
                      {name}
                      <input
                        value={renderVariables[name] ?? ''}
                        onChange={(event) =>
                          setRenderVariables((current) => ({
                            ...current,
                            [name]: event.target.value,
                          }))
                        }
                      />
                    </label>
                  ))}
                </div>
              )}

              <div className="prompt-actions">
                <button
                  className="button-primary"
                  type="button"
                  onClick={() => renderPreview.mutate()}
                  disabled={!renderTarget || renderPreview.isPending}
                >
                  {renderPreview.isPending ? '渲染中…' : '渲染预览'}
                </button>
              </div>

              {renderError && (
                <p className="prompt-error" role="alert">
                  {renderError}
                </p>
              )}

              {renderResult && (
                <div className="prompt-render-result">
                  <p>
                    使用 v{renderResult.version} · 内容哈希{' '}
                    {shortHash(renderResult.content_hash)}
                  </p>
                  <pre>{renderResult.rendered}</pre>
                </div>
              )}
            </section>
          )}

          {activeTab === 'audit' && (
            <section className="prompt-card">
              <header className="prompt-card-head">
                <div>
                  <h2>变更审计</h2>
                  <p>最近 100 条版本注册、发布、灰度与回滚记录。</p>
                </div>
                <span className="prompt-count">{auditRows.length} 条</span>
              </header>

              {auditQuery.isPending && <LoadingState label="正在加载变更审计" />}

              {auditQuery.isError && (
                <ErrorState
                  title="变更审计加载失败"
                  description={errorText(auditQuery.error)}
                  onRetry={() => void auditQuery.refetch()}
                />
              )}

              {auditQuery.isSuccess && auditRows.length === 0 && (
                <EmptyState
                  title="暂无变更记录"
                  description="该提示词还没有注册、发布或灰度操作记录。"
                />
              )}

              {auditRows.length > 0 && (
                <div className="prompt-table-wrap">
                  <table className="prompt-table">
                    <thead>
                      <tr>
                        <th>时间</th>
                        <th>动作</th>
                        <th>操作者</th>
                        <th>详情</th>
                      </tr>
                    </thead>
                    <tbody>
                      {auditRows.slice((auditPage - 1) * 20, auditPage * 20).map((row, index) => (
                        <tr
                          key={String(
                            row.event_id ?? `${row.prompt_id ?? ''}-${index}`,
                          )}
                        >
                          <td className="prompt-nowrap">
                            {formatDateTime(row.created_at)}
                          </td>
                          <td>{actionLabel(row.action)}</td>
                          <td>{row.actor_id ?? '—'}</td>
                          <td>
                            {row.details === null || row.details === undefined ? (
                              '—'
                            ) : (
                              <details className="prompt-detail">
                                <summary>展开</summary>
                                <pre>{localizedJson(row.details)}</pre>
                              </details>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <Pagination total={auditRows.length} page={auditPage} pageSize={20} onPageChange={setAuditPage} />
                </div>
              )}
            </section>
          )}
        </>
      )}
    </section>
  )
}
