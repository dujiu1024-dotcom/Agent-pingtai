import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react'
import type { PropsWithChildren } from 'react'

import { fetchSession, logout as logoutRequest } from '@/lib/auth/session'
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

  const refresh = useCallback(async () => {
    if (!enabled) {
      setStatus('anonymous')
      setSession(null)
      return
    }
    setStatus('loading')
    setError(null)
    try {
      const result = await fetchSession()
      setSession(result)
      setStatus(result ? 'authenticated' : 'anonymous')
    } catch (reason) {
      setSession(null)
      setStatus('anonymous')
      setError(reason instanceof Error ? reason.message : '会话读取失败')
    }
  }, [enabled])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const signOut = useCallback(async () => {
    await logoutRequest()
    await refresh()
  }, [refresh])

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
