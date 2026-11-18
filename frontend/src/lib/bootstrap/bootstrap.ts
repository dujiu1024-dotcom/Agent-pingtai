import { apiClient } from '@/lib/api'

export interface BootstrapConfig {
  auth: {
    enabled: boolean
  }
  version: string
  capabilities: string[]
}

export function parseBootstrapConfig(value: unknown): BootstrapConfig {
  if (!value || typeof value !== 'object') {
    throw new Error('服务端启动配置格式无效')
  }
  const payload = value as Record<string, unknown>
  const auth = payload.auth
  if (!auth || typeof auth !== 'object' || typeof (auth as Record<string, unknown>).enabled !== 'boolean') {
    throw new Error('服务端未提供有效的 auth.enabled 配置')
  }
  const capabilities = Array.isArray(payload.capabilities)
    ? payload.capabilities.filter((item): item is string => typeof item === 'string')
    : []
  return {
    auth: { enabled: (auth as { enabled: boolean }).enabled },
    version: typeof payload.version === 'string' ? payload.version : 'unknown',
    capabilities,
  }
}

/** The server bootstrap is the sole authority for the authentication mode. */
export async function loadBootstrapConfig(): Promise<BootstrapConfig> {
  const response = await apiClient.get<unknown>('/platform/v1/bootstrap', {
    etag: false,
    timeoutMs: 8_000,
  })
  return parseBootstrapConfig(response)
}
