import { describe, expect, it } from 'vitest'

import { parseBootstrapConfig } from './bootstrap'

describe('parseBootstrapConfig', () => {
  it('accepts the public no-auth bootstrap response', () => {
    expect(parseBootstrapConfig({
      auth: { enabled: false },
      version: '1.0.0',
      capabilities: ['office', 'runs'],
    })).toEqual({
      auth: { enabled: false },
      version: '1.0.0',
      capabilities: ['office', 'runs'],
    })
  })

  it('preserves auth-enabled mode from the server', () => {
    expect(parseBootstrapConfig({
      auth: { enabled: true },
      version: '1.0.0',
      capabilities: [],
    }).auth.enabled).toBe(true)
  })

  it('rejects missing or invalid auth mode instead of guessing', () => {
    expect(() => parseBootstrapConfig({ version: '1.0.0' })).toThrow('auth.enabled')
    expect(() => parseBootstrapConfig({ auth: { enabled: 'false' } })).toThrow('auth.enabled')
  })
})
