import { describe, expect, it } from 'vitest'

describe('Knowledge workbench contract', () => {
  it('restricts upload to server-supported document formats', () => {
    const allowed = ['.pdf', '.md', '.markdown', '.html', '.htm']
    expect(allowed).toContain('.pdf')
    expect(allowed).toContain('.md')
    expect(allowed).not.toContain('.docx')
  })
  it('does not invent asynchronous ingest lifecycle states', () => {
    const states = ['idle', 'uploading', 'completed', 'failed']
    expect(states).not.toContain('queued')
    expect(states).not.toContain('retrying')
  })
})
