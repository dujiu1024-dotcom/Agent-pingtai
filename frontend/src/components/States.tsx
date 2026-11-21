import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'

export function EmptyState({
  title = '暂无数据',
  description = '当前没有可展示的内容。',
  action,
}: {
  title?: string
  description?: string
  action?: ReactNode
}) {
  return (
    <section className="state-panel" role="status">
      <span className="state-symbol" aria-hidden="true">
        ◇
      </span>
      <h2>{title}</h2>
      <p>{description}</p>
      {action}
    </section>
  )
}

export function LoadingState({ label = '正在加载' }: { label?: string }) {
  return (
    <section className="state-panel" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <p>{label}…</p>
    </section>
  )
}

export function ErrorState({
  title = '暂时无法加载',
  description,
  onRetry,
}: {
  title?: string
  description: string
  onRetry?: () => void
}) {
  return (
    <section className="state-panel state-panel--error" role="alert">
      <strong className="state-code">!</strong>
      <h2>{title}</h2>
      <p>{description}</p>
      {onRetry && (
        <button className="button-primary" type="button" onClick={onRetry}>
          重试
        </button>
      )}
    </section>
  )
}

export function NotFoundState() {
  return (
    <section className="state-panel">
      <strong className="state-code">404</strong>
      <h2>页面不存在</h2>
      <p>地址可能已变更，或页面尚未开放。</p>
      <Link className="text-link" to="/workspace/overview">
        返回运营驾驶舱
      </Link>
    </section>
  )
}

export function ModeRestrictedState({
  title,
  description,
}: {
  title: string
  description: string
}) {
  return (
    <section className="state-panel">
      <strong className="state-code">—</strong>
      <h2>{title}</h2>
      <p>{description}</p>
      <Link className="text-link" to="/workspace/overview">
        返回运营驾驶舱
      </Link>
    </section>
  )
}


export function ForbiddenState() {
  return (
    <section className="state-panel state-panel--error" role="alert">
      <strong className="state-code">403</strong>
      <h2>无权访问</h2>
      <p>当前会话未获得此页面所需权限。请联系管理员分配权限。</p>
      <Link className="text-link" to="/workspace/overview">
        返回运营驾驶舱
      </Link>
    </section>
  )
}
