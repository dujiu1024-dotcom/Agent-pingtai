import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { EmptyState, ErrorState, LoadingState } from '@/components/States'
import { Pagination } from '@/components/Pagination'
import { ApiError, apiClient } from '@/lib/api'
import { formatDateTime, localizedJson, valueLabel } from '@/lib/i18n/labels'
import './AuditCenterPage.css'

type Tab = 'identity' | 'tool' | 'budget'

type IdentityEvent = {
  audit_id?: string
  actor_id?: string
  action?: string
  subject_id?: string
  outcome?: string
  detail?: unknown
  created_at?: string
}

type ToolEvent = {
  audit_id?: string
  actor_id?: string
  action?: string
  resource?: unknown
  outcome?: string
  run_id?: string
  agent_id?: string
  detail?: unknown
  created_at?: string
}

type BudgetEvent = {
  call_id?: string
  model_key?: string
  status?: string
  estimated_total_tokens?: number | null
  actual_total_tokens?: number | null
  token_delta?: number | null
  estimated_cost_usd?: string | number | null
  actual_cost_usd?: string | number | null
  cost_delta_usd?: string | number | null
  overrun_reason?: string | null
  created_at?: string
  settled_at?: string | null
}

const TABS: { key: Tab; label: string }[] = [
  { key: 'identity', label: '身份事件' },
  { key: 'tool', label: '工具事件' },
  { key: 'budget', label: 'Run 预算事件' },
]

const BUDGET_STATUS_LABELS: Record<string, string> = {
  estimated: '已预估',
  settled: '已结算',
}

const errorText = (error: unknown) => {
  if (error instanceof ApiError) {
    if (error.status === 403) return '当前账号没有审计读取权限。'
    if (error.status === 503) return '服务端未配置该审计数据源，暂不可读取。'
    if (error.status === 400) return '查询参数不合法，请检查运行 ID 等条件。'
  }
  return error instanceof Error && error.message
    ? error.message
    : '请求失败，请稍后重试。'
}

const numberText = (value: unknown) => {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return new Intl.NumberFormat('zh-CN').format(value)
  }
  if (value === null || value === undefined || value === '') return '—'
  return String(value)
}

const costText = (value: unknown) => {
  if (value === null || value === undefined || value === '') return '—'
  const parsed = Number(value)
  return Number.isFinite(parsed) ? `$${parsed.toFixed(4)}` : String(value)
}

const cellText = (value: unknown) => {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'object') return localizedJson(value)
  return valueLabel(value)
}

const detailCell = (value: unknown) => {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'object') {
    return (
      <details className="audit-detail">
        <summary>展开</summary>
        <pre>{localizedJson(value)}</pre>
      </details>
    )
  }
  return valueLabel(value)
}

export function AuditCenterPage() {
  const [tab, setTab] = useState<Tab>('identity')
  const [runIdInput, setRunIdInput] = useState('')
  const [budgetRunId, setBudgetRunId] = useState('')
  const [identityPage, setIdentityPage] = useState(1)
  const [toolPage, setToolPage] = useState(1)
  const [budgetPage, setBudgetPage] = useState(1)

  useEffect(() => { setIdentityPage(1); setToolPage(1); setBudgetPage(1) }, [tab])

  const identityQuery = useQuery({
    queryKey: ['audit-center', 'identity'],
    queryFn: () =>
      apiClient.get<{ items?: IdentityEvent[] }>(
        '/platform/v1/audit/identity-events?limit=100',
      ),
    enabled: tab === 'identity',
    retry: false,
  })

  const toolQuery = useQuery({
    queryKey: ['audit-center', 'tool'],
    queryFn: () =>
      apiClient.get<{ items?: ToolEvent[] }>(
        '/platform/v1/audit/tool-events?limit=100',
      ),
    enabled: tab === 'tool',
    retry: false,
  })

  const budgetQuery = useQuery({
    queryKey: ['audit-center', 'budget', budgetRunId],
    queryFn: () =>
      apiClient.get<{ items?: BudgetEvent[] }>(
        `/platform/v1/audit/run-budget-events?run_id=${encodeURIComponent(budgetRunId)}&limit=100`,
      ),
    enabled: tab === 'budget' && budgetRunId !== '',
    retry: false,
  })

  const identityRows = identityQuery.data?.items ?? []
  const toolRows = toolQuery.data?.items ?? []
  const budgetRows = budgetQuery.data?.items ?? []

  const activeCount =
    tab === 'identity'
      ? identityRows.length
      : tab === 'tool'
        ? toolRows.length
        : budgetRows.length

  const refreshing =
    tab === 'identity'
      ? identityQuery.isFetching
      : tab === 'tool'
        ? toolQuery.isFetching
        : budgetQuery.isFetching

  const refresh = () => {
    if (tab === 'identity') void identityQuery.refetch()
    else if (tab === 'tool') void toolQuery.refetch()
    else if (budgetRunId) void budgetQuery.refetch()
  }

  const submitRunId = (event: FormEvent) => {
    event.preventDefault()
    setBudgetRunId(runIdInput.trim())
  }

  return (
    <section className="page-content audit-center">
      <div className="page-heading">
        <div>
          <p className="eyebrow">全局能力 / 审计中心</p>
          <h1>审计中心</h1>
          <p>
            按身份、工具与 Run 预算三个维度查询当前租户的审计记录。
            数据只读，来自服务端审计存储。
          </p>
        </div>
        <button
          type="button"
          className="button-secondary"
          onClick={refresh}
          disabled={refreshing}
        >
          {refreshing ? '刷新中…' : '刷新数据'}
        </button>
      </div>

      <div className="panel-tabs" role="tablist">
        {TABS.map((item) => (
          <button
            key={item.key}
            className={`panel-tab ${tab === item.key ? 'active' : ''}`}
            type="button"
            role="tab"
            aria-selected={tab === item.key}
            onClick={() => setTab(item.key)}
          >
            {item.label}
            {tab === item.key && activeCount > 0 && (
              <span className="panel-tab-count">{activeCount}</span>
            )}
          </button>
        ))}
      </div>

      {tab === 'identity' && (
        <section className="audit-panel">
          <header className="audit-panel-head">
            <div>
              <h2>身份事件</h2>
              <p>最近 100 条身份审计事件，可进入事件详情查看完整字段。</p>
            </div>
            <span className="audit-count">{identityRows.length} 条</span>
          </header>

          {identityQuery.isPending && <LoadingState label="正在加载身份事件" />}

          {identityQuery.isError && (
            <ErrorState
              title="身份事件加载失败"
              description={errorText(identityQuery.error)}
              onRetry={() => void identityQuery.refetch()}
            />
          )}

          {identityQuery.isSuccess && identityRows.length === 0 && (
            <EmptyState
              title="暂无身份事件"
              description="当前租户还没有身份审计记录。"
            />
          )}

          {identityRows.length > 0 && (
            <div className="audit-table-wrap">
              <table className="audit-table">
                <thead>
                  <tr>
                    <th>时间</th>
                    <th>主体</th>
                    <th>动作</th>
                    <th>结果</th>
                    <th>资源</th>
                    <th>详情</th>
                    <th>事件</th>
                  </tr>
                </thead>
                <tbody>
                  {identityRows.slice((identityPage - 1) * 20, identityPage * 20).map((event, index) => (
                    <tr key={event.audit_id ?? `identity-${index}`}>
                      <td className="audit-nowrap">
                        {formatDateTime(event.created_at)}
                      </td>
                      <td className="audit-nowrap">{event.actor_id ?? '—'}</td>
                      <td>{cellText(event.action)}</td>
                      <td>{valueLabel(event.outcome)}</td>
                      <td>{event.subject_id ?? '—'}</td>
                      <td>{detailCell(event.detail)}</td>
                      <td className="audit-nowrap">
                        {event.audit_id ? (
                          <Link
                            className="text-link"
                            to={`/admin/audit/${encodeURIComponent(event.audit_id)}`}
                          >
                            查看事件
                          </Link>
                        ) : (
                          '—'
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <Pagination total={identityRows.length} page={identityPage} pageSize={20} onPageChange={setIdentityPage} />
            </div>
          )}
        </section>
      )}

      {tab === 'tool' && (
        <section className="audit-panel">
          <header className="audit-panel-head">
            <div>
              <h2>工具事件</h2>
              <p>最近 100 条工具调用审计事件，包含调用主体、资源与运行关联。</p>
            </div>
            <span className="audit-count">{toolRows.length} 条</span>
          </header>

          {toolQuery.isPending && <LoadingState label="正在加载工具事件" />}

          {toolQuery.isError && (
            <ErrorState
              title="工具事件加载失败"
              description={errorText(toolQuery.error)}
              onRetry={() => void toolQuery.refetch()}
            />
          )}

          {toolQuery.isSuccess && toolRows.length === 0 && (
            <EmptyState
              title="暂无工具事件"
              description="当前租户还没有工具调用审计记录。"
            />
          )}

          {toolRows.length > 0 && (
            <div className="audit-table-wrap">
              <table className="audit-table">
                <thead>
                  <tr>
                    <th>时间</th>
                    <th>主体</th>
                    <th>动作</th>
                    <th>结果</th>
                    <th>资源</th>
                    <th>运行</th>
                    <th>详情</th>
                  </tr>
                </thead>
                <tbody>
                  {toolRows.slice((toolPage - 1) * 20, toolPage * 20).map((event, index) => (
                    <tr key={event.audit_id ?? `tool-${index}`}>
                      <td className="audit-nowrap">
                        {formatDateTime(event.created_at)}
                      </td>
                      <td className="audit-nowrap">{event.actor_id ?? '—'}</td>
                      <td>{cellText(event.action)}</td>
                      <td>{valueLabel(event.outcome)}</td>
                      <td>{cellText(event.resource)}</td>
                      <td className="audit-nowrap">
                        {event.run_id ?? event.agent_id ?? '—'}
                      </td>
                      <td>{detailCell(event.detail)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <Pagination total={toolRows.length} page={toolPage} pageSize={20} onPageChange={setToolPage} />
            </div>
          )}
        </section>
      )}

      {tab === 'budget' && (
        <section className="audit-panel">
          <header className="audit-panel-head">
            <div>
              <h2>Run 预算事件</h2>
              <p>按运行 ID 查询模型调用的预估与实际预算消耗记录。</p>
            </div>
            <span className="audit-count">{budgetRows.length} 条</span>
          </header>

          <form className="audit-run-form" onSubmit={submitRunId}>
            <label>
              运行 ID
              <input
                value={runIdInput}
                onChange={(event) => setRunIdInput(event.target.value)}
                placeholder="输入 Run 的 UUID"
              />
            </label>
            <button
              className="button-primary"
              type="submit"
              disabled={!runIdInput.trim()}
            >
              查询预算事件
            </button>
          </form>

          {budgetRunId === '' ? (
            <EmptyState
              title="等待运行 ID"
              description="Run 预算事件按运行维度记录，请输入运行 ID 后查询。"
            />
          ) : budgetQuery.isPending ? (
            <LoadingState label="正在加载预算事件" />
          ) : budgetQuery.isError ? (
            <ErrorState
              title="预算事件加载失败"
              description={errorText(budgetQuery.error)}
              onRetry={() => void budgetQuery.refetch()}
            />
          ) : budgetRows.length === 0 ? (
            <EmptyState
              title="暂无预算事件"
              description="该运行没有模型调用预算记录，或记录尚未写入。"
            />
          ) : (
            <div className="audit-table-wrap">
              <table className="audit-table">
                <thead>
                  <tr>
                    <th>时间</th>
                    <th>模型</th>
                    <th>状态</th>
                    <th>预估 Token</th>
                    <th>实际 Token</th>
                    <th>Token 差额</th>
                    <th>预估成本</th>
                    <th>实际成本</th>
                    <th>超限原因</th>
                    <th>结算时间</th>
                  </tr>
                </thead>
                <tbody>
                  {budgetRows.slice((budgetPage - 1) * 20, budgetPage * 20).map((event, index) => (
                    <tr key={event.call_id ?? `budget-${index}`}>
                      <td className="audit-nowrap">
                        {formatDateTime(event.created_at)}
                      </td>
                      <td>{event.model_key ?? '—'}</td>
                      <td className="audit-nowrap">
                        {event.status
                          ? (BUDGET_STATUS_LABELS[event.status] ??
                            valueLabel(event.status))
                          : '—'}
                      </td>
                      <td>{numberText(event.estimated_total_tokens)}</td>
                      <td>{numberText(event.actual_total_tokens)}</td>
                      <td>{numberText(event.token_delta)}</td>
                      <td>{costText(event.estimated_cost_usd)}</td>
                      <td>{costText(event.actual_cost_usd)}</td>
                      <td>{event.overrun_reason ?? '—'}</td>
                      <td className="audit-nowrap">
                        {formatDateTime(event.settled_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <Pagination total={budgetRows.length} page={budgetPage} pageSize={20} onPageChange={setBudgetPage} />
            </div>
          )}
        </section>
      )}
    </section>
  )
}
