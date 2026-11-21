import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { ErrorState, LoadingState } from '@/components/States'
import { apiClient } from '@/lib/api/api-client'

interface NotificationItem {
  id: string
  kind: string
  level: 'info' | 'warning' | 'error'
  title: string
  detail: string
  at?: string | null
  path: string
}

export function NotificationsPage() {
  const query = useQuery({
    queryKey: ['notifications'],
    queryFn: () =>
      apiClient.get<{ items: NotificationItem[]; count: number }>(
        '/platform/v1/notifications',
      ),
    refetchInterval: 30_000,
  })
  const items = query.data?.items ?? []

  return (
    <section className="page-content">
      <div className="page-heading">
        <p className="eyebrow">通知</p>
        <h1>通知中心</h1>
        <p>汇总待审批与死信运行，数据每 30 秒刷新一次。</p>
      </div>
      {query.isPending && <LoadingState />}
      {query.isError && (
        <ErrorState
          description={
            query.error instanceof Error ? query.error.message : '加载失败'
          }
          onRetry={() => void query.refetch()}
        />
      )}
      {!query.isPending && !query.isError && items.length === 0 && (
        <p className="detail-empty">当前没有待处理通知。</p>
      )}
      {items.length > 0 && (
        <ul className="notification-list">
          {items.map((item) => (
            <li key={`${item.kind}-${item.id}`}>
              <Link className="notification-row" to={item.path}>
                <span
                  className={`notification-level notification-level--${item.level}`}
                  aria-hidden="true"
                />
                <span className="notification-body">
                  <strong>{item.title}</strong>
                  <small>{item.detail}</small>
                </span>
                <span className="notification-time">
                  {item.at ? new Date(item.at).toLocaleString('zh-CN') : ''}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
