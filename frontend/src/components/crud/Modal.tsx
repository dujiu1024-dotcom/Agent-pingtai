import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import type { ReactNode } from 'react'

import type { FieldDef, FormValues } from './types'

export function Modal({
  title,
  onClose,
  children,
  footer,
}: {
  title: string
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  return createPortal(
    <div
      className="modal-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div
        className="modal-card"
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <header className="modal-head">
          <h2>{title}</h2>
          <button
            className="modal-close"
            type="button"
            aria-label="关闭"
            onClick={onClose}
          >
            ×
          </button>
        </header>
        <div className="modal-body">{children}</div>
        {footer && <footer className="modal-foot">{footer}</footer>}
      </div>
    </div>,
    document.body,
  )
}

export function RecordForm({
  formId,
  fields,
  values,
  onChange,
  onFileChange,
  onSubmit,
}: {
  formId: string
  fields: FieldDef[]
  values: FormValues
  onChange: (key: string, value: string | number | boolean) => void
  onFileChange: (file: File | null) => void
  onSubmit: () => void
}) {
  return (
    <form
      id={formId}
      className="crud-form"
      onSubmit={(event) => {
        event.preventDefault()
        onSubmit()
      }}
    >
      {fields.map((field) => {
        const type = field.type ?? 'text'
        const value = values[field.key]
        const inputId = `${formId}-${field.key}`
        return (
          <label
            className={
              type === 'textarea' || type === 'json'
                ? 'crud-field crud-field--wide'
                : 'crud-field'
            }
            key={field.key}
            htmlFor={inputId}
          >
            <span>
              {field.label}
              {field.required && <em className="crud-required">*</em>}
            </span>
            {type === 'textarea' && (
              <textarea
                id={inputId}
                rows={field.rows ?? 4}
                value={String(value ?? '')}
                placeholder={field.placeholder}
                onChange={(event) => onChange(field.key, event.target.value)}
              />
            )}
            {type === 'json' && (
              <textarea
                id={inputId}
                className="crud-json"
                rows={field.rows ?? 10}
                value={String(value ?? '')}
                placeholder={field.placeholder}
                onChange={(event) => onChange(field.key, event.target.value)}
              />
            )}
            {type === 'boolean' && (
              <span className="crud-checkbox">
                <input
                  id={inputId}
                  type="checkbox"
                  checked={Boolean(value)}
                  onChange={(event) =>
                    onChange(field.key, event.target.checked)
                  }
                />
                <span>{field.placeholder ?? '启用'}</span>
              </span>
            )}
            {type === 'select' && (
              <select
                id={inputId}
                value={String(value ?? '')}
                onChange={(event) => onChange(field.key, event.target.value)}
              >
                {(field.options ?? []).map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            )}
            {type === 'number' && (
              <input
                id={inputId}
                type="number"
                value={String(value ?? '')}
                placeholder={field.placeholder}
                onChange={(event) => onChange(field.key, event.target.value)}
              />
            )}
            {type === 'file' && (
              <input
                id={inputId}
                type="file"
                onChange={(event) =>
                  onFileChange(event.target.files?.[0] ?? null)
                }
              />
            )}
            {type === 'text' && (
              <input
                id={inputId}
                type="text"
                value={String(value ?? '')}
                placeholder={field.placeholder}
                onChange={(event) => onChange(field.key, event.target.value)}
              />
            )}
            {field.help && <small className="crud-help">{field.help}</small>}
          </label>
        )
      })}
    </form>
  )
}
