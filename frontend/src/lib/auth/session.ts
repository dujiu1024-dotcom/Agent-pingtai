import { ApiClient, ApiError } from '@/lib/api/api-client'

/** Identity endpoints live at the origin root (`/auth/...`), not under `/api`. */
export const authClient = new ApiClient({ baseUrl: '/' })

export interface SessionInfo {
  tenant_id: string
  principal_type: 'user' | 'service_account'
  user_id?: string
  email?: string
  display_name?: string
  service_account_id?: string
  agent_id?: string
  /** Supplied by POST /auth/login; /auth/me does not currently expose expiry. */
  expires_at?: string
}

export interface LoginResponse {
  authenticated: true
  tenant_id: string
  expires_at: string
  csrf_header: 'X-CSRF-Token'
}

export function parseSessionInfo(value: unknown): SessionInfo {
  if (!value || typeof value !== 'object') {
    throw new Error('会话响应格式无效')
  }
  const payload = value as Record<string, unknown>
  if (
    typeof payload.tenant_id !== 'string' ||
    !payload.tenant_id ||
    (payload.principal_type !== 'user' && payload.principal_type !== 'service_account')
  ) {
    throw new Error('会话响应缺少有效的租户或主体信息')
  }
  if (payload.principal_type === 'user' && (
    typeof payload.user_id !== 'string' ||
    typeof payload.email !== 'string' ||
    typeof payload.display_name !== 'string'
  )) {
    throw new Error('会话用户信息格式无效')
  }
  if (payload.principal_type === 'service_account' && (
    typeof payload.service_account_id !== 'string' ||
    typeof payload.agent_id !== 'string'
  )) {
    throw new Error('服务账号会话信息格式无效')
  }
  return value as SessionInfo
}

export async function fetchSession(): Promise<SessionInfo | null> {
  try {
    const result = await authClient.get<unknown>('/auth/me', { etag: false })
    return parseSessionInfo(result)
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return null
    throw error
  }
}

export async function login(input: {
  tenantId: string
  email: string
  password: string
}): Promise<LoginResponse> {
  authClient.clearEtagCache()
  const result = await authClient.post<LoginResponse>('/auth/login', {
    tenant_id: input.tenantId.trim(),
    email: input.email.trim(),
    password: input.password,
  })
  if (
    result?.authenticated !== true ||
    typeof result.tenant_id !== 'string' ||
    typeof result.expires_at !== 'string' ||
    result.csrf_header !== 'X-CSRF-Token'
  ) {
    throw new Error('登录响应缺少有效的会话信息')
  }
  return result
}

export async function logout(): Promise<void> {
  authClient.clearEtagCache()
  try {
    await authClient.post('/auth/logout')
  } catch (error) {
    if (!(error instanceof ApiError && error.status === 401)) throw error
  }
}

export async function initializePlatform(input: {
  tenantId: string
  tenantName: string
  email: string
  displayName: string
  password: string
}): Promise<void> {
  await authClient.post('/auth/initialize', {
    tenant_id: input.tenantId.trim(),
    tenant_name: input.tenantName.trim(),
    email: input.email.trim(),
    display_name: input.displayName.trim(),
    password: input.password,
  })
}
