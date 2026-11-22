import { renderToStaticMarkup } from 'react-dom/server'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { Button } from './Button'
import { Card } from './Card'
import { Modal } from './Modal'
import { Table } from './Table'

// Modal uses a portal in the browser. Keep the render test focused on its
// accessible dialog markup without requiring a browser or another DOM package.
vi.mock('react-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-dom')>()
  return {
    ...actual,
    createPortal: (children: ReactNode) => children,
  }
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('design system component rendering', () => {
  it('renders Button variant, content, and loading accessibility state', () => {
    const markup = renderToStaticMarkup(
      <Button variant="danger" size="lg" loading>
        Delete item
      </Button>,
    )

    expect(markup).toContain('ui-button--danger')
    expect(markup).toContain('ui-button--lg')
    expect(markup).toContain('is-loading')
    expect(markup).toContain('aria-busy="true"')
    expect(markup).toContain('disabled=""')
    expect(markup).toContain('Delete item')
  })

  it('renders Card heading, description, actions, and body content', () => {
    const markup = renderToStaticMarkup(
      <Card
        as="article"
        padding="lg"
        title="Usage overview"
        description="Current billing period"
        actions={<button type="button">View details</button>}
      >
        <p>42,000 tokens</p>
      </Card>,
    )

    expect(markup).toContain('<article')
    expect(markup).toContain('ui-card--padding-lg')
    expect(markup).toContain('<h3 class="ui-card__title">Usage overview</h3>')
    expect(markup).toContain('Current billing period')
    expect(markup).toContain('View details')
    expect(markup).toContain('42,000 tokens')
  })

  it('renders Table headers, rows, caption, and custom cell renderers', () => {
    const markup = renderToStaticMarkup(
      <Table
        caption="Recent runs"
        density="compact"
        columns={[
          { key: 'name', title: 'Run name' },
          {
            key: 'status',
            title: 'Status',
            render: (row: { status: string }) => <strong>{row.status}</strong>,
          },
        ]}
        data={[{ id: 'run-1', name: 'Daily report', status: 'Succeeded' }]}
        rowKey={(row: { id: string }) => row.id}
      />,
    )

    expect(markup).toContain('<caption class="ui-table__caption">Recent runs</caption>')
    expect(markup).toContain('ui-table-wrap--compact')
    expect(markup).toContain('<th scope="col"')
    expect(markup).toContain('Run name')
    expect(markup).toContain('Daily report')
    expect(markup).toContain('<strong>Succeeded</strong>')
  })

  it('renders an open Modal as an accessible dialog through its portal boundary', () => {
    vi.stubGlobal('document', { body: {} })

    const markup = renderToStaticMarkup(
      <Modal
        open
        onClose={() => undefined}
        title="Confirm deletion"
        description="This action cannot be undone."
        footer={<button type="button">Confirm</button>}
      >
        <p>Delete the selected record?</p>
      </Modal>,
    )

    expect(markup).toContain('role="dialog"')
    expect(markup).toContain('aria-modal="true"')
    expect(markup).toContain('aria-labelledby="ui-modal-title"')
    expect(markup).toContain('<h2 id="ui-modal-title">Confirm deletion</h2>')
    expect(markup).toContain('This action cannot be undone.')
    expect(markup).toContain('Delete the selected record?')
    expect(markup).toContain('aria-label="关闭对话框"')
    expect(markup).toContain('>Confirm</button>')
  })
})
