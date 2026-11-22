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
  /** Present only when the identity API publishes authoritative grants. */
  permissions?: string[]
  /** ISO-8601 timestamp when this session expires, if known. */
  expires_at?: string
}

export async function fetchSession(): Promise<SessionInfo | null> {
  try {
    return await authClient.get<SessionInfo>('/auth/me')
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
  await authClient.post('/auth/login', {
    tenant_id: input.tenantId.trim(),
    email: input.email.trim(),
    password: input.password,
  })
}

export async function logout(): Promise<void> {
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
