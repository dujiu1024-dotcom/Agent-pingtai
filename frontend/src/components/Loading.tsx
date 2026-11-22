import type { HTMLAttributes } from 'react'

export interface LoadingProps extends HTMLAttributes<HTMLDivElement> {
  label?: string
  size?: 'sm' | 'md' | 'lg'
  fullPage?: boolean
}

export function Loading({
  label = '正在加载',
  size = 'md',
  fullPage = false,
  className,
  ...props
}: LoadingProps) {
  return (
    <div
      {...props}
      className={[
        'ui-loading',
        `ui-loading--${size}`,
        fullPage && 'ui-loading--page',
        className,
      ]
        .filter(Boolean)
        .join(' ')}
      role="status"
      aria-live="polite"
    >
      <span className="ui-loading__spinner" aria-hidden="true" />
      <span>{label}</span>
    </div>
  )
}
