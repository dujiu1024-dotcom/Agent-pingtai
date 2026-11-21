import { useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom'

import { initializePlatform, login } from '@/lib/auth/session'
import { useAuth } from '@/providers/AuthProvider'
import { LoadingState } from '@/components/States'

function AuthLayout({
  title,
  description,
  children,
  footer,
}: {
  title: string
  description: string
  children: ReactNode
  footer?: ReactNode
}) {
  return (
    <div className="auth-screen">
      <section className="auth-card" aria-labelledby="auth-title">
        <div className="auth-brand">
          <span className="brand-mark" aria-hidden="true">
            A
          </span>
          <div>
            <p className="eyebrow">龍奕 FDE 工作室</p>
            <h1 id="auth-title">{title}</h1>
            <p>{description}</p>
          </div>
        </div>
        {children}
        {footer}
      </section>
    </div>
  )
}

export function LoginPage() {
  const { enabled, status, refresh, error: sessionError } = useAuth()
  const location = useLocation()
  const [tenantId, setTenantId] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  if (!enabled) {
    return <Navigate to="/workspace/overview" replace />
  }
  if (status === 'loading') return <div className="bootstrap-screen"><LoadingState label="正在校验登录会话" /></div>
  if (status === 'authenticated') {
    const from = (location.state as { from?: string } | null)?.from
    const safeFrom = from?.startsWith('/') && !from.startsWith('//') ? from : undefined
    return <Navigate to={safeFrom || '/workspace/overview'} replace />
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      const issued = await login({ tenantId, email, password })
      await refresh(issued.expires_at)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '登录失败')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <AuthLayout
      title="登录龍奕 FDE 工作室"
      description="使用租户、邮箱与密码进入工作空间。"
      footer={
        <p className="auth-footer">
          首次部署？<Link to="/auth/initialize">初始化管理员</Link>
        </p>
      }
    >
      <form className="auth-form" onSubmit={onSubmit}>
        <label className="auth-field">
          <span>租户 ID</span>
          <input
            value={tenantId}
            onChange={(event) => setTenantId(event.target.value)}
            autoComplete="organization"
            required
            placeholder="例如 acme"
          />
        </label>
        <label className="auth-field">
          <span>邮箱</span>
          <input
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            autoComplete="username"
            required
          />
        </label>
        <label className="auth-field">
          <span>密码</span>
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
            required
          />
        </label>
        {(error || sessionError) && (
          <p className="auth-error" role="alert">
            {error}
          </p>
        )}
        <button className="button-primary" type="submit" disabled={submitting}>
          {submitting ? '正在登录…' : '登录'}
        </button>
      </form>
    </AuthLayout>
  )
}

export function InitializePage() {
  const { enabled, status, refresh } = useAuth()
  const navigate = useNavigate()
  const [tenantId, setTenantId] = useState('')
  const [tenantName, setTenantName] = useState('')
  const [email, setEmail] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  if (!enabled) {
    return <Navigate to="/workspace/overview" replace />
  }
  if (status === 'loading') return <div className="bootstrap-screen"><LoadingState label="正在校验登录会话" /></div>
  if (status === 'authenticated') {
    return <Navigate to="/workspace/overview" replace />
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    if (password.length < 12) {
      setError('密码至少需要 12 个字符')
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      await initializePlatform({
        tenantId,
        tenantName,
        email,
        displayName,
        password,
      })
      const issued = await login({ tenantId, email, password })
      await refresh(issued.expires_at)
      navigate('/workspace/overview', { replace: true })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '初始化失败')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <AuthLayout
      title="初始化平台管理员"
      description="仅在实例尚无任何租户时可用，将创建租户与首个所有者账号。"
      footer={
        <p className="auth-footer">
          已有账号？<Link to="/auth/login">返回登录</Link>
        </p>
      }
    >
      <form className="auth-form" onSubmit={onSubmit}>
        <label className="auth-field">
          <span>租户 ID</span>
          <input
            value={tenantId}
            onChange={(event) => setTenantId(event.target.value)}
            required
            placeholder="例如 acme"
          />
        </label>
        <label className="auth-field">
          <span>租户名称</span>
          <input
            value={tenantName}
            onChange={(event) => setTenantName(event.target.value)}
            placeholder="例如 Acme 智能运营"
          />
        </label>
        <label className="auth-field">
          <span>管理员邮箱</span>
          <input
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            required
            autoComplete="username"
          />
        </label>
        <label className="auth-field">
          <span>管理员名称</span>
          <input
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
            placeholder="显示名称"
          />
        </label>
        <label className="auth-field">
          <span>密码（至少 12 位）</span>
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
            minLength={12}
            autoComplete="new-password"
          />
        </label>
        {error && (
          <p className="auth-error" role="alert">
            {error}
          </p>
        )}
        <button className="button-primary" type="submit" disabled={submitting}>
          {submitting ? '正在初始化…' : '创建管理员并登录'}
        </button>
      </form>
    </AuthLayout>
  )
}
