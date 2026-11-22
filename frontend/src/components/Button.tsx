import type { ButtonHTMLAttributes, ReactNode } from 'react'

export type ButtonVariant = 'primary' | 'secondary' | 'tertiary' | 'danger' | 'ghost'
export type ButtonSize = 'sm' | 'md' | 'lg'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  loading?: boolean
  leadingIcon?: ReactNode
  trailingIcon?: ReactNode
}

export function Button({ variant = 'primary', size = 'md', loading = false, disabled, leadingIcon, trailingIcon, children, className, type = 'button', ...props }: ButtonProps) {
  const classes = ['ui-button', `ui-button--${variant}`, `ui-button--${size}`, loading && 'is-loading', className].filter(Boolean).join(' ')
  return (
    <button {...props} className={classes} type={type} disabled={disabled || loading} aria-busy={loading || undefined}>
      {loading ? <span className="ui-button__spinner" aria-hidden="true" /> : leadingIcon}
      <span>{children}</span>
      {!loading && trailingIcon}
    </button>
  )
}
