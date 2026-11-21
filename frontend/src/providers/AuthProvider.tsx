import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
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
  refresh: (sessionExpiresAt?: string) => Promise<void>
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
  const sessionRef = useRef<SessionInfo | null>(null)
  const [error, setError] = useState<string | null>(null)
  const queryClient = useQueryClient()

  const refresh = useCallback(async (sessionExpiresAt?: string) => {
    if (!enabled) {
      setStatus('anonymous')
      setSession(null)
      sessionRef.current = null
      return
    }
    if (!sessionRef.current) setStatus('loading')
    setError(null)
    try {
      const previousSession = sessionRef.current
      const result = await fetchSession()
      const nextSession = result
        ? {
            ...result,
            expires_at:
              result.expires_at ?? sessionExpiresAt ?? sessionRef.current?.expires_at,
          }
        : null
      setSession(nextSession)
      sessionRef.current = nextSession
      setStatus(nextSession ? 'authenticated' : 'anonymous')
      if (!result || (!previousSession && nextSession)) {
        queryClient.clear()
        apiClient.clearEtagCache()
      }
    } catch (reason) {
      if (!sessionRef.current) setStatus('anonymous')
      setError(reason instanceof Error ? reason.message : '会话读取失败')
    }
  }, [enabled, queryClient])

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

  useEffect(() => {
    if (!enabled || !session?.expires_at) return
    const remaining = Date.parse(session.expires_at) - Date.now()
    if (!Number.isFinite(remaining)) return
    const timer = window.setTimeout(() => {
      const current = sessionRef.current
      if (current) {
        const activeSession = { ...current, expires_at: undefined }
        sessionRef.current = activeSession
        setSession(activeSession)
      }
      void refresh()
    }, Math.max(0, remaining))
    return () => window.clearTimeout(timer)
  }, [enabled, refresh, session?.expires_at])

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
