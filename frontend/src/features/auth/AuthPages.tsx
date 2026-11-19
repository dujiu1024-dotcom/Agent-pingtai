import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { ApiError, apiClient } from '@/lib/api'

type LoginResponse = {
  authenticated: boolean
  tenant_id: string
  expires_at: string
  csrf_header: string
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
      const result = await apiClient.post<LoginResponse>('/auth/login', {
        email: email.trim(),
        password,
        tenant_id: tenantId.trim(),
      }, { etag: false, timeoutMs: 10_000 })
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
        <div className="auth-brand"><span className="auth-brand-mark">A</span><span>Agent Platform</span></div>
        <p className="eyebrow">SECURE WORKSPACE</p>
        <h1 id="login-title">登录工作空间</h1>
        <p className="auth-description">使用已启用的组织账号继续。登录状态由服务端身份服务验证。</p>
        <form className="auth-form" onSubmit={submit}>
          <label>工作邮箱<input type="email" autoComplete="username" required maxLength={320} value={email} onChange={(event) => setEmail(event.target.value)} /></label>
          <label>租户 ID<input type="text" autoComplete="organization" required maxLength={128} value={tenantId} onChange={(event) => setTenantId(event.target.value)} /></label>
          <label>密码<input type="password" autoComplete="current-password" required maxLength={1024} value={password} onChange={(event) => setPassword(event.target.value)} /></label>
          {error && <p className="auth-error" role="alert">{error}</p>}
          <button className="auth-submit" type="submit" disabled={busy}>{busy ? '正在验证…' : '登录'}</button>
        </form>
        <p className="auth-footnote">登录凭据仅提交至当前平台 API；会话由 HttpOnly Cookie 管理。</p>
        <Link className="auth-back-link" to="/workspace/overview">返回工作空间</Link>
      </section>
    </main>
  )
}

export function AdminInitPage() {
  return (
    <main className="auth-screen">
      <section className="auth-card" aria-labelledby="init-title">
        <div className="auth-brand"><span className="auth-brand-mark">A</span><span>Agent Platform</span></div>
        <p className="eyebrow">FIRST-TIME SETUP</p>
        <h1 id="init-title">管理员初始化</h1>
        <p className="auth-description">此入口仅在服务端权限模式下显示。</p>
        <div className="auth-notice" role="status">
          <strong>初始化 API 尚未提供</strong>
          <p>当前 identity router 仅公开登录、登出和当前身份接口，没有管理员初始化端点。为避免创建未受服务端保护的管理员账号，此页面不会提交或保存初始化数据。</p>
        </div>
        <Link className="auth-submit auth-submit-link" to="/auth/login">前往登录</Link>
      </section>
    </main>
  )
}
