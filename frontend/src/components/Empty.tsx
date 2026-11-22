import type { ReactNode } from 'react'
import { Inbox } from 'lucide-react'

export interface EmptyProps {
  title?: string
  description?: string
  action?: ReactNode
  icon?: ReactNode
  compact?: boolean
}

export function Empty({ title = '暂无数据', description = '当前没有可展示的内容。', action, icon, compact = false }: EmptyProps) {
  return (
    <section className={compact ? 'ui-empty ui-empty--compact' : 'ui-empty'} role="status">
      <span className="ui-empty__icon" aria-hidden="true">{icon ?? <Inbox size={24} />}</span>
      <h3>{title}</h3><p>{description}</p>
      {action && <div className="ui-empty__action">{action}</div>}
    </section>
  )
}
