import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { ApiError, apiClient } from '@/lib/api'

type LoginResponse = {
  authenticated: boolean
  tenant_id: string
  expires_at: string
  csrf_header: string
}

type InitializeResponse = {
  initialized: boolean
  tenant_id: string
  user_id: string
}

export function LoginPage() {
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [tenantId, setTenantId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const result = await apiClient.post<LoginResponse>(
        '/auth/login',
        {
          email: email.trim(),
          password,
          tenant_id: tenantId.trim(),
        },
        { etag: false, timeoutMs: 10_000 },
      )
      if (!result.authenticated) throw new Error('认证服务未确认登录状态')
      navigate('/workspace/overview', { replace: true })
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) {
        setError('邮箱、密码或租户信息不正确')
      } else if (reason instanceof Error) {
        setError(reason.message)
      } else {
        setError('登录请求失败，请稍后重试')
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="auth-screen">
      <section className="auth-card" aria-labelledby="login-title">
        <div className="auth-brand">
          <span className="auth-brand-mark">A</span>
          <span>Agent Platform</span>
        </div>
        <p className="eyebrow">SECURE WORKSPACE</p>
        <h1 id="login-title">登录工作空间</h1>
        <p className="auth-description">
          使用已启用的组织账号继续。登录状态由服务端身份服务验证。
        </p>
        <form className="auth-form" onSubmit={submit}>
          <label>
            工作邮箱
            <input
              type="email"
              autoComplete="username"
              required
              maxLength={320}
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </label>
          <label>
            租户 ID
            <input
              type="text"
              autoComplete="organization"
              required
              maxLength={128}
              value={tenantId}
              onChange={(event) => setTenantId(event.target.value)}
            />
          </label>
          <label>
            密码
            <input
              type="password"
              autoComplete="current-password"
              required
              maxLength={1024}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </label>
          {error && <p className="auth-error" role="alert">{error}</p>}
          <button className="auth-submit" type="submit" disabled={busy}>
            {busy ? '正在验证…' : '登录'}
          </button>
        </form>
        <p className="auth-footnote">
          登录凭据仅提交至当前平台 API；会话由 HttpOnly Cookie 管理。
        </p>
        <Link className="auth-back-link" to="/workspace/overview">
          返回工作空间
        </Link>
      </section>
    </main>
  )
}

export function AdminInitPage() {
  const [tenantId, setTenantId] = useState('')
  const [tenantName, setTenantName] = useState('')
  const [email, setEmail] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [initializedTenant, setInitializedTenant] = useState<string | null>(null)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    if (password.length < 12) {
      setError('管理员密码至少需要 12 个字符')
      return
    }
    if (password !== confirmation) {
      setError('两次输入的密码不一致')
      return
    }

    setBusy(true)
    try {
      const result = await apiClient.post<InitializeResponse>(
        '/auth/initialize',
        {
          tenant_id: tenantId.trim(),
          tenant_name: tenantName.trim() || tenantId.trim(),
          email: email.trim(),
          display_name: displayName.trim() || email.trim(),
          password,
        },
        { etag: false, timeoutMs: 20_000 },
      )
      if (!result.initialized) {
        throw new Error('服务端未确认管理员初始化结果')
      }
      setInitializedTenant(result.tenant_id)
      setPassword('')
      setConfirmation('')
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 409) {
        setError('平台已完成初始化，不能重复创建首位管理员')
      } else if (reason instanceof ApiError && reason.status === 503) {
        setError('服务端尚未配置管理员初始化连接，请联系部署管理员')
      } else if (reason instanceof ApiError && reason.status === 400) {
        setError(reason.message)
      } else if (reason instanceof Error) {
        setError(reason.message)
      } else {
        setError('管理员初始化请求失败，请稍后重试')
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="auth-screen">
      <section className="auth-card" aria-labelledby="init-title">
        <div className="auth-brand">
          <span className="auth-brand-mark">A</span>
          <span>Agent Platform</span>
        </div>
        <p className="eyebrow">FIRST-TIME SETUP</p>
        <h1 id="init-title">管理员初始化</h1>
        <p className="auth-description">
          仅当平台尚未初始化时，创建首个租户及租户管理员。服务端会原子校验并拒绝重复初始化。
        </p>
        {initializedTenant ? (
          <div className="auth-notice" role="status">
            <strong>管理员初始化成功</strong>
            <p>租户 {initializedTenant} 已创建。请使用刚设置的邮箱、租户 ID 和密码登录。</p>
            <Link className="auth-submit auth-submit-link" to="/auth/login">
              前往登录
            </Link>
          </div>
        ) : (
          <form className="auth-form" onSubmit={submit}>
            <label>
              租户 ID
              <input
                type="text"
                autoComplete="off"
                required
                maxLength={128}
                value={tenantId}
                onChange={(event) => setTenantId(event.target.value)}
              />
            </label>
            <label>
              租户名称
              <input
                type="text"
                autoComplete="organization"
                maxLength={200}
                value={tenantName}
                onChange={(event) => setTenantName(event.target.value)}
              />
            </label>
            <label>
              管理员邮箱
              <input
                type="email"
                autoComplete="username"
                required
                maxLength={320}
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
            </label>
            <label>
              管理员姓名
              <input
                type="text"
                autoComplete="name"
                maxLength={200}
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
              />
            </label>
            <label>
              管理员密码（至少 12 位）
              <input
                type="password"
                autoComplete="new-password"
                required
                minLength={12}
                maxLength={1024}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
            </label>
            <label>
              确认密码
              <input
                type="password"
                autoComplete="new-password"
                required
                minLength={12}
                maxLength={1024}
                value={confirmation}
                onChange={(event) => setConfirmation(event.target.value)}
              />
            </label>
            {error && <p className="auth-error" role="alert">{error}</p>}
            <button className="auth-submit" type="submit" disabled={busy}>
              {busy ? '正在创建管理员…' : '创建首位管理员'}
            </button>
          </form>
        )}
        <p className="auth-footnote">
          密码仅通过当前平台 API 提交，不写入浏览器存储。平台是否已初始化由服务端事务校验。
        </p>
        {!initializedTenant && (
          <Link className="auth-back-link" to="/auth/login">
            返回登录
          </Link>
        )}
      </section>
    </main>
  )
}
