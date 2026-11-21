import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

import {
  findNavigationSection,
  findSubPage,
  getNavigationSections,
  isCanonicalRouteSetValid,
} from '@/routes/route-config'

const source = (path: string) =>
  readFileSync(new URL(path, import.meta.url), 'utf8')

describe('P-UI-001 frontend foundation contracts', () => {
  it('loads the canonical design tokens globally before application styles', () => {
    const entry = source('./main.tsx')
    const tokens = source('./components/design-system.css')
    const theme = source('./theme.css')

    expect(entry).toContain("import '@/components/design-system.css'")
    expect(entry).toContain("import '@/theme.css'")
    expect(tokens).toContain('--color-brand-600: #356df3')
    expect(tokens).toContain('--color-success: #16845b')
    expect(tokens).toContain('--space-4: 16px')
    expect(theme).toContain(":root[data-theme='office']")
    expect(theme).toContain('--canvas: var(--office-wall)')
    expect(theme).toContain('--focus-outline')
  })

  it('exposes the three navigation layers without duplicating canonical routes', () => {
    const sections = getNavigationSections(false)
    expect(sections.map((section) => section.id)).toEqual([
      'home',
      'office',
      'tasks',
      'collaboration',
      'workflow',
      'cost',
      'administration',
    ])
    expect(findNavigationSection('/workspace/tasks/42')?.id).toBe('tasks')
    expect(
      findSubPage(
        findNavigationSection('/workspace/tasks'),
        '/workspace/tasks',
      )?.label,
    ).toBe('任务列表')
    expect(isCanonicalRouteSetValid()).toBe(true)
  })

  it('keeps authentication-only navigation conditional on server auth mode', () => {
    const noAuth = getNavigationSections(false)
    const auth = getNavigationSections(true)
    const noAuthAdmin = noAuth.find((section) => section.id === 'administration')
    const authAdmin = auth.find((section) => section.id === 'administration')

    expect(noAuthAdmin?.subpages.some((page) => page.path === '/admin/access-control')).toBe(false)
    expect(authAdmin?.subpages.some((page) => page.path === '/admin/access-control')).toBe(true)
  })

  it('keeps shared loading, empty, error and fatal boundary states accessible', () => {
    const states = source('./components/States.tsx')
    const boundary = source('./components/ErrorBoundary.tsx')

    expect(states).toContain('role="status"')
    expect(states).toContain('aria-live="polite"')
    expect(states).toContain('role="alert"')
    expect(states).toContain('onRetry')
    expect(boundary).toContain('getDerivedStateFromError')
    expect(boundary).toContain('页面发生异常')
  })
})
