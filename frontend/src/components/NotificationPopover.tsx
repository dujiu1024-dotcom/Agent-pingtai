import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Bell, ChevronRight } from 'lucide-react'

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

export function NotificationPopover({ onClose }: { onClose: () => void }) {
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
    <section className="notification-popover" aria-label="最近通知">
      <header className="notification-popover-header">
        <div>
          <strong>通知</strong>
          <small>{query.data?.count ?? items.length} 条通知</small>
        </div>
        <button className="text-link" type="button" onClick={() => void query.refetch()}>
          刷新
        </button>
      </header>
      <div className="notification-popover-content">
        {query.isPending && <LoadingState label="正在加载通知" />}
        {query.isError && (
          <ErrorState
            description={query.error instanceof Error ? query.error.message : '通知加载失败'}
            onRetry={() => void query.refetch()}
          />
        )}
        {!query.isPending && !query.isError && items.length === 0 && (
          <p className="detail-empty">当前没有待处理通知。</p>
        )}
        {items.slice(0, 6).map((item) => (
          <Link
            className="notification-popover-item"
            key={`${item.kind}-${item.id}`}
            to={item.path}
            onClick={onClose}
          >
            <span className={`notification-level notification-level--${item.level}`} aria-hidden="true" />
            <span className="notification-body">
              <strong>{item.title}</strong>
              <small>{item.detail}</small>
              {item.at && <small>{new Date(item.at).toLocaleString('zh-CN')}</small>}
            </span>
            <ChevronRight size={15} aria-hidden="true" />
          </Link>
        ))}
      </div>
      <footer className="notification-popover-footer">
        <Link to="/notifications" onClick={onClose}>查看全部通知</Link>
      </footer>
    </section>
  )
}
