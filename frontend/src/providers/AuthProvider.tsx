import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react'
import type { PropsWithChildren } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { fetchSession, logout as logoutRequest } from '@/lib/auth/session'
import { apiClient } from '@/lib/api/api-client'
import type { SessionInfo } from '@/lib/auth/session'

type AuthStatus = 'loading' | 'authenticated' | 'anonymous'

interface AuthContextValue {
  enabled: boolean
  status: AuthStatus
  session: SessionInfo | null
  error: string | null
  refresh: () => Promise<void>
  signOut: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({
  enabled,
  children,
}: PropsWithChildren<{ enabled: boolean }>) {
  const [status, setStatus] = useState<AuthStatus>(
    enabled ? 'loading' : 'anonymous',
  )
  const [session, setSession] = useState<SessionInfo | null>(null)
  const [error, setError] = useState<string | null>(null)
  const queryClient = useQueryClient()

  const refresh = useCallback(async () => {
    if (!enabled) {
      setStatus('anonymous')
      setSession(null)
      return
    }
    if (!session) setStatus('loading')
    setError(null)
    try {
      const result = await fetchSession()
      setSession(result)
      setStatus(result ? 'authenticated' : 'anonymous')
      if (!result) {
        queryClient.clear()
        apiClient.clearEtagCache()
      }
    } catch (reason) {
      if (!session) setStatus('anonymous')
      setError(reason instanceof Error ? reason.message : '会话读取失败')
    }
  }, [enabled, queryClient, session])

  useEffect(() => {
    void refresh()
    if (!enabled) return
    const onFocus = () => {
      if (document.visibilityState === 'visible') void refresh()
    }
    window.addEventListener('focus', onFocus)
    document.addEventListener('visibilitychange', onFocus)
    return () => {
      window.removeEventListener('focus', onFocus)
      document.removeEventListener('visibilitychange', onFocus)
    }
  }, [enabled, refresh])

  const signOut = useCallback(async () => {
    await logoutRequest()
    queryClient.clear()
    apiClient.clearEtagCache()
    await refresh()
  }, [queryClient, refresh])

  const value = useMemo<AuthContextValue>(
    () => ({ enabled, status, session, error, refresh, signOut }),
    [enabled, status, session, error, refresh, signOut],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside AuthProvider')
  return value
}
