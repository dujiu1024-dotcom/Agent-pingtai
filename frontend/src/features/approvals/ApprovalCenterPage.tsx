import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { EmptyState, ErrorState, LoadingState } from '@/components/States'
import { apiClient } from '@/lib/api'

type ApprovalRecord = {
  approval_id: string
  run_id?: string | null
  action: string
  resource: string
  risk_level: string
  requested_by: string
  decided_by?: string | null
  status: string
  expires_at: string
  action_hash?: string
}

type ApprovalListResponse = { items: ApprovalRecord[] }

const APPROVALS_API = '/platform/v1/approvals'

const riskLabels: Record<string, string> = {
  low: '低风险',
  medium: '中风险',
  high: '高风险',
  critical: '严重风险',
}

const statusLabels: Record<string, string> = {
  pending: '待审批',
  approved: '已批准',
  rejected: '已拒绝',
  revoked: '已撤销',
  expired: '已过期',
}

function formatDate(value?: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function formatError(error: unknown): string {
  return error instanceof Error ? error.message : '请求失败，请稍后重试。'
}

function StatusPill({ value }: { value: string }) {
  const tone =
    value === 'approved'
      ? 'success'
      : value === 'rejected' || value === 'revoked' || value === 'expired'
        ? 'muted'
        : 'pending'
  return (
    <span className={`approval-status approval-status--${tone}`}>
      {statusLabels[value] ?? value}
    </span>
  )
}

function RiskPill({ value }: { value: string }) {
  return (
    <span className={`approval-risk approval-risk--${value}`}>
      {riskLabels[value] ?? value}
    </span>
  )
}

export function ApprovalCenterPage({ authEnabled }: { authEnabled: boolean }) {
  const { approvalId } = useParams()
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')

  const listQuery = useQuery({
    queryKey: ['approval-center', 'list'],
    queryFn: () => apiClient.get<ApprovalListResponse>(APPROVALS_API),
    enabled: authEnabled && !approvalId,
    refetchInterval: 15000,
  })

  const detailQuery = useQuery({
    queryKey: ['approval-center', 'detail', approvalId],
    queryFn: () =>
      apiClient.get<ApprovalRecord>(
        `${APPROVALS_API}/${encodeURIComponent(approvalId ?? '')}`,
      ),
    enabled: authEnabled && Boolean(approvalId),
  })

  const items = listQuery.data?.items ?? []
  const filtered = useMemo(
    () =>
      items.filter((item) => {
        const text = [
          item.approval_id,
          item.action,
          item.resource,
          item.requested_by,
          item.run_id ?? '',
        ]
          .join(' ')
          .toLowerCase()
        return (
          (!search || text.includes(search.trim().toLowerCase())) &&
          (!status || item.status === status)
        )
      }),
    [items, search, status],
  )

  if (!authEnabled) {
    return (
      <section className="page-content approval-center">
        <div className="page-heading">
          <p className="eyebrow">ADMINISTRATION / APPROVALS</p>
          <h1>审批中心</h1>
          <p>查看待处理审批及历史审批记录。</p>
        </div>
        <EmptyState
          title="当前模式不提供审批数据"
          description="服务端 auth.enabled=false。审批 API 需要已认证身份，本页面不会发起请求。"
        />
      </section>
    )
  }

  if (approvalId) {
    if (detailQuery.isPending) return <LoadingState label="正在加载审批详情" />
    if (detailQuery.isError) {
      return (
        <section className="page-content">
          <ErrorState
            title="审批详情加载失败"
            description={formatError(detailQuery.error)}
            onRetry={() => void detailQuery.refetch()}
          />
        </section>
      )
    }
    const record = detailQuery.data
    return (
      <section className="page-content approval-center">
        <Link className="text-link" to="/admin/approvals">
          ← 返回审批中心
        </Link>
        <div className="page-heading">
          <p className="eyebrow">APPROVAL / {record.approval_id}</p>
          <h1>审批详情</h1>
          <p>展示服务端返回的审批元数据与动作摘要指纹。</p>
        </div>
        <section className="approval-detail-panel">
          <div className="approval-detail-header">
            <div>
              <span className="approval-field-label">审批状态</span>
              <StatusPill value={record.status} />
            </div>
            <RiskPill value={record.risk_level} />
          </div>
          <dl className="approval-detail-grid">
            <div><dt>Approval ID</dt><dd>{record.approval_id}</dd></div>
            <div><dt>Run ID</dt><dd>{record.run_id ?? '—'}</dd></div>
            <div><dt>动作</dt><dd>{record.action}</dd></div>
            <div><dt>资源</dt><dd>{record.resource}</dd></div>
            <div><dt>申请人</dt><dd>{record.requested_by}</dd></div>
            <div><dt>审批人</dt><dd>{record.decided_by ?? '—'}</dd></div>
            <div><dt>到期时间</dt><dd>{formatDate(record.expires_at)}</dd></div>
            <div><dt>动作摘要指纹</dt><dd><code>{record.action_hash ?? '服务端未返回'}</code></dd></div>
          </dl>
        </section>
        <p className="approval-data-note">
          出于最小披露原则，详情页不展示或重建原始 parameters；批准、拒绝和撤销操作由既有受控流程处理，本页面仅提供只读查询。
        </p>
      </section>
    )
  }

  if (listQuery.isPending) return <LoadingState label="正在加载审批列表" />
  if (listQuery.isError) {
    return (
      <section className="page-content">
        <ErrorState
          title="审批列表加载失败"
          description={formatError(listQuery.error)}
          onRetry={() => void listQuery.refetch()}
        />
      </section>
    )
  }

  return (
    <section className="page-content approval-center">
      <div className="page-heading approval-heading">
        <div>
          <p className="eyebrow">ADMINISTRATION / APPROVALS</p>
          <h1>审批中心</h1>
          <p>查看当前身份在租户范围内可见的审批请求与处理状态。</p>
        </div>
        <span className="approval-count">共 {items.length} 条</span>
      </div>
      <div className="approval-toolbar">
        <input
          aria-label="搜索审批"
          placeholder="搜索动作、资源、申请人或 ID"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <select
          aria-label="按状态筛选"
          value={status}
          onChange={(event) => setStatus(event.target.value)}
        >
          <option value="">全部状态</option>
          {Object.entries(statusLabels).map(([value, label]) => (
            <option key={value} value={value}>{label}</option>
          ))}
        </select>
        <button type="button" className="button-secondary" onClick={() => void listQuery.refetch()}>
          刷新
        </button>
      </div>
      {filtered.length === 0 ? (
        <EmptyState
          title={items.length ? '没有匹配的审批' : '暂无待处理审批'}
          description={items.length ? '调整搜索词或状态筛选。' : '当前身份没有可见的审批请求。'}
        />
      ) : (
        <div className="approval-table-wrap">
          <table className="approval-table">
            <thead>
              <tr>
                <th>动作 / 资源</th>
                <th>风险等级</th>
                <th>申请人</th>
                <th>状态</th>
                <th>到期时间</th>
                <th>Run</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((item) => (
                <tr key={item.approval_id}>
                  <td>
                    <Link to={`/admin/approvals/${encodeURIComponent(item.approval_id)}`}>
                      <strong>{item.action}</strong>
                    </Link>
                    <small>{item.resource}</small>
                    <small>{item.approval_id}</small>
                  </td>
                  <td><RiskPill value={item.risk_level} /></td>
                  <td>{item.requested_by}</td>
                  <td><StatusPill value={item.status} /></td>
                  <td>{formatDate(item.expires_at)}</td>
                  <td>{item.run_id ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="approval-data-note">
        列表数据由服务端按 tenant 与审批权限过滤；当前界面只读，不提供绕过服务端授权的审批操作。
      </p>
    </section>
  )
}
