import type { HTMLAttributes, ReactNode } from 'react'

export interface CardProps extends HTMLAttributes<HTMLElement> {
  title?: ReactNode
  description?: ReactNode
  actions?: ReactNode
  padding?: 'none' | 'sm' | 'md' | 'lg'
  as?: 'section' | 'article' | 'div'
}

export function Card({
  title,
  description,
  actions,
  padding = 'md',
  as: Element = 'section',
  className,
  children,
  ...props
}: CardProps) {
  return (
    <Element
      {...props}
      className={[
        'ui-card',
        `ui-card--padding-${padding}`,
        className,
      ]
        .filter(Boolean)
        .join(' ')}
    >
      {(title || description || actions) && (
        <header className="ui-card__header">
          <div className="ui-card__heading">
            {title && <h3 className="ui-card__title">{title}</h3>}
            {description && (
              <p className="ui-card__description">{description}</p>
            )}
          </div>
          {actions && <div className="ui-card__actions">{actions}</div>}
        </header>
      )}
      <div className="ui-card__body">{children}</div>
    </Element>
  )
}
