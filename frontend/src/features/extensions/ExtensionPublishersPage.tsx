import { useState } from 'react'
import type { FormEvent } from 'react'

import { EmptyState, ErrorState, LoadingState } from '@/components/States'
import { ApiError, apiClient } from '@/lib/api'
import { formatDateTime } from '@/lib/i18n/labels'
import './ExtensionPublishersPage.css'

type RegisterResult = {
  tenant_id?: string
  extension_id?: string
  publisher_id?: string
  key_id?: string
  not_before?: number | null
  expires_at?: number | null
  revoked?: boolean
}

type RevokeResult = {
  extension_id?: string
  key_id?: string
  revoked?: boolean
}

type OperationResult =
  | { kind: 'register'; data: RegisterResult }
  | { kind: 'revoke'; data: RevokeResult }

type Busy = 'register' | 'revoke' | null

const GENERIC_ERRORS = new Set([
  'The request was rejected',
  'The request could not be completed',
])

const errorText = (error: unknown) => {
  if (error instanceof ApiError) {
    if (error.status === 403)
      return '当前账号没有 tenant.manage 权限，无法管理扩展发布者。'
    if (error.status === 503)
      return '服务端审计写入或注册表暂不可用，请稍后重试。'
    if (error.status === 400) {
      return error.message && !GENERIC_ERRORS.has(error.message)
        ? `请求参数不合法：${error.message}`
        : '请求参数不合法，请检查扩展 ID、密钥 ID 与公钥 PEM 格式。'
    }
    return error.message || '请求失败，请稍后重试。'
  }
  return error instanceof Error && error.message
    ? error.message
    : '请求失败，请稍后重试。'
}

const unixTime = (value: number | null | undefined) => {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return `${formatDateTime(new Date(value * 1000).toISOString())}（${value}）`
}

const boolText = (value: unknown) =>
  value === true ? '是' : value === false ? '否' : '—'

const resultRow = (label: string, value: unknown) => (
  <div key={label}>
    <dt>{label}</dt>
    <dd>{value === null || value === undefined || value === '' ? '—' : String(value)}</dd>
  </div>
)

type PublishersTab = 'register' | 'revoke'

export function ExtensionPublishersPage() {
  const [tab, setTab] = useState<PublishersTab>('register')
  const [extensionId, setExtensionId] = useState('')
  const [publisherId, setPublisherId] = useState('')
  const [keyId, setKeyId] = useState('')
  const [publicKeyPem, setPublicKeyPem] = useState('')
  const [notBefore, setNotBefore] = useState('0')
  const [expiresAt, setExpiresAt] = useState('')
  const [revokeKeyIds, setRevokeKeyIds] = useState('')
  const [targetExtensionId, setTargetExtensionId] = useState('')
  const [targetKeyId, setTargetKeyId] = useState('')
  const [busy, setBusy] = useState<Busy>(null)
  const [result, setResult] = useState<OperationResult | null>(null)
  const [error, setError] = useState('')

  const submitRegister = async (event: FormEvent) => {
    event.preventDefault()
    setError('')
    setResult(null)
    const extension = extensionId.trim()
    const publisher = publisherId.trim()
    const key = keyId.trim()
    if (!extension || !publisher || !key) {
      setError('请填写扩展 ID、发布者 ID 与密钥 ID。')
      return
    }
    if (!publicKeyPem.trim()) {
      setError('请粘贴 PEM 格式公钥。')
      return
    }
    const notBeforeValue = Number(notBefore)
    if (notBefore.trim() === '' || !Number.isFinite(notBeforeValue)) {
      setError('生效时间需填写 Unix 秒时间戳。')
      return
    }
    const expiresValue = expiresAt.trim() === '' ? null : Number(expiresAt)
    if (
      expiresValue !== null &&
      (!Number.isFinite(expiresValue) || expiresValue <= notBeforeValue)
    ) {
      setError('过期时间需晚于生效时间，且为 Unix 秒时间戳。')
      return
    }
    const revokedIds = revokeKeyIds
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean)
    setBusy('register')
    try {
      const data = await apiClient.post<RegisterResult>(
        '/platform/v1/extensions/publishers/keys',
        {
          extension_id: extension,
          publisher_id: publisher,
          key_id: key,
          public_key_pem: publicKeyPem,
          not_before: notBeforeValue,
          expires_at: expiresValue,
          revoke_key_ids: revokedIds,
        },
      )
      setResult({ kind: 'register', data })
      setPublicKeyPem('')
      setRevokeKeyIds('')
    } catch (reason) {
      setError(errorText(reason))
    } finally {
      setBusy(null)
    }
  }

  const submitRevoke = async (event: FormEvent) => {
    event.preventDefault()
    setError('')
    setResult(null)
    const extension = targetExtensionId.trim()
    const key = targetKeyId.trim()
    if (!extension || !key) {
      setError('请填写要撤销的扩展 ID 与密钥 ID。')
      return
    }
    setBusy('revoke')
    try {
      const data = await apiClient.post<RevokeResult>(
        `/platform/v1/extensions/publishers/${encodeURIComponent(extension)}/keys/${encodeURIComponent(key)}/revoke`,
      )
      setResult({ kind: 'revoke', data })
    } catch (reason) {
      setError(errorText(reason))
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="page-content publishers-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">平台管理 / 扩展发布者</p>
          <h1>扩展发布者</h1>
          <p>按密钥登记、轮换或撤销扩展发布者公钥，结果以服务端返回为准。</p>
        </div>
      </div>

      <p className="publishers-notice">
        扩展发布者当前仅支持按键登记与撤销，不提供密钥列表查询。
      </p>

      <nav className="panel-tabs" role="tablist" aria-label="扩展发布者操作分区">
        <button type="button" role="tab" aria-selected={tab === 'register'} className={tab === 'register' ? 'panel-tab active' : 'panel-tab'} onClick={() => setTab('register')}>
          登记 / 轮换密钥
        </button>
        <button type="button" role="tab" aria-selected={tab === 'revoke'} className={tab === 'revoke' ? 'panel-tab active' : 'panel-tab'} onClick={() => setTab('revoke')}>
          撤销密钥
        </button>
      </nav>

      {tab === 'register' && (
        <section className="publishers-card">
          <header>
            <h2>登记或轮换密钥</h2>
            <p>公钥仅用于提交，不会在页面上回显；填写撤销密钥 ID 时按轮换处理。</p>
          </header>
          <form
            className="publishers-form"
            onSubmit={(event) => void submitRegister(event)}
          >
            <div className="publishers-row">
              <label>
                扩展 ID
                <input
                  value={extensionId}
                  onChange={(event) => setExtensionId(event.target.value)}
                  placeholder="例如 sample-extension"
                  autoComplete="off"
                />
              </label>
              <label>
                发布者 ID
                <input
                  value={publisherId}
                  onChange={(event) => setPublisherId(event.target.value)}
                  placeholder="例如 publisher-a"
                  autoComplete="off"
                />
              </label>
            </div>
            <div className="publishers-row">
              <label>
                密钥 ID
                <input
                  value={keyId}
                  onChange={(event) => setKeyId(event.target.value)}
                  placeholder="例如 key-2026-01"
                  autoComplete="off"
                />
              </label>
              <label>
                生效时间（Unix 秒）
                <input
                  type="number"
                  step={1}
                  value={notBefore}
                  onChange={(event) => setNotBefore(event.target.value)}
                />
              </label>
            </div>
            <label>
              公钥 PEM
              <textarea
                value={publicKeyPem}
                onChange={(event) => setPublicKeyPem(event.target.value)}
                placeholder={'-----BEGIN PUBLIC KEY-----'}
                rows={7}
                spellCheck={false}
                autoComplete="off"
              />
            </label>
            <div className="publishers-row">
              <label>
                过期时间（Unix 秒，可空）
                <input
                  type="number"
                  step={1}
                  value={expiresAt}
                  onChange={(event) => setExpiresAt(event.target.value)}
                  placeholder="留空表示不过期"
                />
              </label>
              <label>
                撤销密钥 ID（逗号分隔，可选）
                <input
                  value={revokeKeyIds}
                  onChange={(event) => setRevokeKeyIds(event.target.value)}
                  placeholder="旧密钥 ID，可填多个"
                />
              </label>
            </div>
            <p className="publishers-hint">
              撤销密钥 ID 不为空时，服务端会先登记新密钥再撤销列出的旧密钥；新密钥不能撤销自身。
            </p>
            <div className="publishers-actions">
              <button
                className="button-primary"
                type="submit"
                disabled={busy !== null}
              >
                {busy === 'register' ? '提交中…' : '登记 / 轮换'}
              </button>
            </div>
          </form>
        </section>
      )}

      {tab === 'revoke' && (
        <>
          <section className="publishers-card">
            <header>
              <h2>撤销密钥</h2>
              <p>按扩展 ID 与密钥 ID 撤销已登记的发布者密钥。</p>
            </header>
          <form
            className="publishers-form"
            onSubmit={(event) => void submitRevoke(event)}
          >
            <label>
              扩展 ID
              <input
                value={targetExtensionId}
                onChange={(event) => setTargetExtensionId(event.target.value)}
                placeholder="例如 sample-extension"
                autoComplete="off"
              />
            </label>
            <label>
              密钥 ID
              <input
                value={targetKeyId}
                onChange={(event) => setTargetKeyId(event.target.value)}
                placeholder="例如 key-2026-01"
                autoComplete="off"
              />
            </label>
            <div className="publishers-actions">
              <button
                className="button-primary"
                type="submit"
                disabled={busy !== null}
              >
                {busy === 'revoke' ? '撤销中…' : '撤销密钥'}
              </button>
            </div>
          </form>
        </section>

          <section className="publishers-card">
            <header>
              <h2>最近操作结果</h2>
              <p>展示服务端返回的键信息，不包含公钥正文。</p>
            </header>
        {busy !== null ? (
          <LoadingState label="正在提交" />
        ) : error ? (
          <ErrorState title="操作失败" description={error} />
        ) : !result ? (
          <EmptyState
            title="尚未执行操作"
            description="提交登记、轮换或撤销后，这里会显示服务端返回的结果。"
          />
        ) : (
          <dl className="publishers-result">
            {result.kind === 'register' ? (
              <>
                {resultRow('租户 ID', result.data.tenant_id)}
                {resultRow('扩展 ID', result.data.extension_id)}
                {resultRow('发布者 ID', result.data.publisher_id)}
                {resultRow('密钥 ID', result.data.key_id)}
                {resultRow('生效时间', unixTime(result.data.not_before))}
                {resultRow('过期时间', unixTime(result.data.expires_at))}
                {resultRow('已撤销', boolText(result.data.revoked))}
              </>
            ) : (
              <>
                {resultRow('扩展 ID', result.data.extension_id)}
                {resultRow('密钥 ID', result.data.key_id)}
                {resultRow('撤销结果', boolText(result.data.revoked))}
              </>
            )}
          </dl>
        )}
      </section>
        </>
      )}
    </section>
  )
}
