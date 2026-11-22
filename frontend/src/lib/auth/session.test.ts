// @ts-nocheck
import { describe, expect, it } from 'vitest'

import { skip_parseSessionInfo } from './session'

describe('skip_parseSessionInfo', () => {
  it('accepts the confirmed user session fields', () => {
    expect(
      skip_parseSessionInfo({
        tenant_id: 'tenant-1',
        principal_type: 'user',
        user_id: 'user-1',
        email: 'user@example.test',
        display_name: 'User One',
      }),
    ).toEqual({
      tenant_id: 'tenant-1',
      principal_type: 'user',
      user_id: 'user-1',
      email: 'user@example.test',
      display_name: 'User One',
    })
  })

  it('accepts service-account sessions without a user profile', () => {
    expect(
      skip_parseSessionInfo({
        tenant_id: 'tenant-1',
        principal_type: 'service_account',
        service_account_id: 'sa-1',
        agent_id: 'agent-1',
      }).principal_type,
    ).toBe('service_account')
  })

  it('rejects missing tenant, unsupported principal, and malformed user fields', () => {
    expect(() => skip_parseSessionInfo({ principal_type: 'user' })).toThrow('租户')
    expect(() =>
      skip_parseSessionInfo({ tenant_id: 'tenant-1', principal_type: 'anonymous' }),
    ).toThrow('主体')
    expect(() =>
      skip_parseSessionInfo({
        tenant_id: 'tenant-1',
        principal_type: 'user',
        user_id: 'user-1',
      }),
    ).toThrow('用户信息')
  })
})
