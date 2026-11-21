import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { EmptyState, ErrorState, LoadingState } from '@/components/States'
import { ApiError, apiClient } from '@/lib/api'
import { formatDateTime, localizedJson, valueLabel } from '@/lib/i18n/labels'
import './AlertsPage.css'

type AlertEvent = {
  event_id: string
  scope_key: string
  rule_id: string
  metric_name: string
  observed_value: number
  threshold: number
  severity: string
  fired_at: string
  description: string
  status: string
  acknowledged_by: string | null
  acknowledged_at: string | null
  resolved_at: string | null
  on_call_user_id: string | null
  ack_due_at: string | null
  delivery_status: string
  delivery_attempts: number
  delivery_max_attempts: number
  next_delivery_at: string | null
  delivered_at: string | null
  last_delivery_error: string | null
}

type AlertListResponse = { items: AlertEvent[]; count: number }

type AlertAction = 'acknowledge' | 'resolve'

const SEVERITY_LABELS: Record<string, string> = {
  info: '提示',
  warning: '警告',
  critical: '严重',
}

const STATUS_LABELS: Record<string, string> = {
  open: '待处理',
  acknowledged: '已确认',
  resolved: '已解决',
}

const severityLabel = (value: string) => SEVERITY_LABELS[value] ?? valueLabel(value)

const statusLabel = (value: string) => STATUS_LABELS[value] ?? valueLabel(value)

const errorText = (error: unknown) => {
  if (error instanceof ApiError && error.status === 403)
    return '当前账号没有告警处理权限（需要告警管理权限）。'
  if (error instanceof ApiError && error.status === 503)
    return '服务端未启用告警存储，或依赖暂不可用。'
  if (error instanceof ApiError && error.status === 404)
    return '该告警已不存在或状态已变化，请刷新列表后重试。'
  return error instanceof Error ? error.message : '请求失败，请稍后重试。'
}

export function AlertsPage() {
  const queryClient = useQueryClient()
  const [actionError, setActionError] = useState('')

  const alertsQuery = useQuery({
    queryKey: ['observability', 'alerts'],
    queryFn: () =>
      apiClient.get<AlertListResponse>('/platform/v1/observability/alerts?limit=100'),
    retry: false,
  })

  const action = useMutation({
    mutationFn: ({ eventId, name }: { eventId: string; name: AlertAction }) =>
      apiClient.post<{ event_id: string; status: string }>(
        `/platform/v1/observability/alerts/${encodeURIComponent(eventId)}/${name}`,
      ),
    onSuccess: async () => {
      setActionError('')
      await queryClient.invalidateQueries({ queryKey: ['observability', 'alerts'] })
    },
    onError: (error) => setActionError(errorText(error)),
  })

  const runAction = (alert: AlertEvent, name: AlertAction) => {
    const confirmText =
      name === 'acknowledge'
        ? '确认该告警？确认后状态将变为“已确认”。'
        : '将该告警标记为已解决？解决后将从待处理列表中移除。'
    if (!window.confirm(confirmText)) return
    action.mutate({ eventId: alert.event_id, name })
  }

  const alerts = alertsQuery.data?.items ?? []
  const count = alertsQuery.data?.count ?? alerts.length

  return (
    <section className="page-content alerts-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">智能体运营 / 告警中心</p>
          <h1>告警中心</h1>
          <p>查看当前租户的未关闭告警，并执行确认或标记解决。数据来自服务端告警事件存储。</p>
        </div>
        {alertsQuery.isSuccess && <span className="alerts-count">待处理 {count} 条</span>}
      </div>

      {actionError && (
        <p className="alerts-action-error" role="alert">
          {actionError}
        </p>
      )}

      {alertsQuery.isPending && <LoadingState label="正在加载告警" />}

      {alertsQuery.isError && (
        <ErrorState
          title="告警列表加载失败"
          description={errorText(alertsQuery.error)}
          onRetry={() => void alertsQuery.refetch()}
        />
      )}

      {alertsQuery.isSuccess && alerts.length === 0 && (
        <EmptyState
          title="暂无未关闭告警"
          description="当前租户没有待处理或已确认的告警。指标触发后会显示在这里。"
        />
      )}

      {alertsQuery.isSuccess && alerts.length > 0 && (
        <div className="alerts-list">
          {alerts.map((alert) => (
            <article
              key={alert.event_id}
              className={`alert-card alert-severity-${alert.severity}`}
            >
              <header className="alert-card-head">
                <div>
                  <h2>{alert.description || alert.rule_id}</h2>
                  <p>
                    指标 {alert.metric_name} · 规则 {alert.rule_id}
                  </p>
                </div>
                <div className="alert-badges">
                  <span className={`alert-badge alert-badge--${alert.severity}`}>
                    {severityLabel(alert.severity)}
                  </span>
                  <span className={`alert-badge alert-badge--status-${alert.status}`}>
                    {statusLabel(alert.status)}
                  </span>
                </div>
              </header>

              <dl className="alert-meta">
                <div>
                  <dt>观测值 / 阈值</dt>
                  <dd>
                    {alert.observed_value} / {alert.threshold}
                  </dd>
                </div>
                <div>
                  <dt>发生时间</dt>
                  <dd>{formatDateTime(alert.fired_at)}</dd>
                </div>
                <div>
                  <dt>确认截止</dt>
                  <dd>{formatDateTime(alert.ack_due_at)}</dd>
                </div>
                <div>
                  <dt>确认人</dt>
                  <dd>{alert.acknowledged_by ?? '—'}</dd>
                </div>
                <div>
                  <dt>确认时间</dt>
                  <dd>{formatDateTime(alert.acknowledged_at)}</dd>
                </div>
                <div>
                  <dt>解决时间</dt>
                  <dd>{formatDateTime(alert.resolved_at)}</dd>
                </div>
              </dl>

              <div className="alert-actions">
                <button
                  className="button-secondary"
                  type="button"
                  disabled={alert.status !== 'open' || action.isPending}
                  onClick={() => runAction(alert, 'acknowledge')}
                >
                  确认告警
                </button>
                <button
                  className="button-primary"
                  type="button"
                  disabled={alert.status === 'resolved' || action.isPending}
                  onClick={() => runAction(alert, 'resolve')}
                >
                  标记已解决
                </button>
              </div>

              <details className="alert-detail">
                <summary>查看告警详情</summary>
                <pre>{localizedJson(alert)}</pre>
              </details>
            </article>
          ))}
        </div>
      )}
    </section>
  )
}
