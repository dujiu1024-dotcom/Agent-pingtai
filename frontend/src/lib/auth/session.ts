import { ApiClient, ApiError } from '@/lib/api/api-client'

/** Identity endpoints live at the origin root (`/auth/...`), not under `/api`. */
export const authClient = new ApiClient({ baseUrl: '/' })

export interface SessionUser {
  user_id: string
  email: string
  display_name: string
}

export interface SessionInfo {
  tenant_id: string
  principal_type: 'user' | 'service_account'
  user?: SessionUser
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
  if (payload.user !== undefined) {
    const user = payload.user as Record<string, unknown> | null
    if (
      !user ||
      typeof user.user_id !== 'string' ||
      typeof user.email !== 'string' ||
      typeof user.display_name !== 'string'
    ) {
      throw new Error('会话用户信息格式无效')
    }
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
}): Promise<void> {
  authClient.clearEtagCache()
  await authClient.post('/auth/login', {
    tenant_id: input.tenantId.trim(),
    email: input.email.trim(),
    password: input.password,
  })
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
