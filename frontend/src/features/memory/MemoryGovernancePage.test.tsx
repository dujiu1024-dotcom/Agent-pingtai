import { describe, expect, it } from 'vitest'
import { matchesMemoryTab } from './MemoryGovernancePage'

const item = {
  memory_id: 'mem-1',
  scope: 'user',
  scope_id: 'user-1',
  content: 'prefers concise answers',
  source_ref: 'conversation:123',
  confidence: 0.9,
  version: 2,
  status: 'active',
  expires_at: null,
}

describe('memory governance filters', () => {
  it('includes candidate and conflict in the review tab', () => {
    expect(matchesMemoryTab({ ...item, status: 'candidate' }, 'candidate')).toBe(true)
    expect(matchesMemoryTab({ ...item, status: 'conflict' }, 'candidate')).toBe(true)
    expect(matchesMemoryTab(item, 'candidate')).toBe(false)
  })

  it('uses expiry timestamps rather than guessing status', () => {
    expect(matchesMemoryTab({ ...item, expires_at: '2026-01-01T00:00:00Z' }, 'expired', Date.parse('2026-02-01T00:00:00Z'))).toBe(true)
    expect(matchesMemoryTab({ ...item, expires_at: '2027-01-01T00:00:00Z' }, 'expired', Date.parse('2026-02-01T00:00:00Z'))).toBe(false)
    expect(matchesMemoryTab(item, 'expired', Date.parse('2026-02-01T00:00:00Z'))).toBe(false)
  })

  it('keeps archived and rejected tabs tied to explicit server states', () => {
    expect(matchesMemoryTab({ ...item, status: 'archived' }, 'archived')).toBe(true)
    expect(matchesMemoryTab({ ...item, status: 'rejected' }, 'rejected')).toBe(true)
    expect(matchesMemoryTab({ ...item, status: 'active' }, 'archived')).toBe(false)
  })
})
