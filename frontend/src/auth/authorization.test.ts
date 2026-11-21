import { describe, expect, it } from 'vitest'

import {
  canAccess,
  hasAllPermissions,
  hasAnyPermission,
  hasPermission,
} from './authorization'

describe('frontend permission policy', () => {
  const subject = { permissions: ['platform.rbac.read', 'task.run'] }

  it('checks exact permission identifiers', () => {
    expect(hasPermission(subject, 'platform.rbac.read')).toBe(true)
    expect(hasPermission(subject, 'platform.rbac.write')).toBe(false)
  })

  it('supports all/any permission checks', () => {
    expect(hasAllPermissions(subject, ['task.run', 'platform.rbac.read'])).toBe(true)
    expect(hasAllPermissions(subject, ['task.run', 'task.delete'])).toBe(false)
    expect(hasAnyPermission(subject, ['task.delete', 'task.run'])).toBe(true)
    expect(hasAnyPermission(subject, [])).toBe(true)
  })

  it('fails closed when auth is enabled and permissions are absent', () => {
    expect(canAccess(true, null, ['platform.rbac.read'])).toBe(false)
    expect(canAccess(true, {}, ['platform.rbac.read'])).toBe(false)
    expect(canAccess(true, subject, ['platform.rbac.read'])).toBe(true)
  })

  it('does not impose identity permissions in server no-auth mode', () => {
    expect(canAccess(false, null, ['platform.rbac.read'])).toBe(true)
  })
})
