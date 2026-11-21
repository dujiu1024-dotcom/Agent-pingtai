import type { ReactNode } from 'react'

import { canAccess } from './authorization'
import { useAuth } from '@/providers/AuthProvider'

export function Can({
  permission,
  permissions,
  fallback = null,
  children,
}: {
  permission?: string
  permissions?: readonly string[]
  fallback?: ReactNode
  children: ReactNode
}) {
  const auth = useAuth()
  const required = permissions ?? (permission ? [permission] : [])
  return canAccess(auth.enabled, auth.session, required) ? (
    <>{children}</>
  ) : (
    <>{fallback}</>
  )
}
