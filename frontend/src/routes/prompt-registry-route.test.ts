import { describe, expect, it } from 'vitest'
import { matchRoutes } from 'react-router-dom'

import { detailRoutes, getVisibleNavigation, pageRoutes } from './route-config'

describe('Prompt Registry routes', () => {
  it('shows navigation only when the server advertises prompt management', () => {
    const withoutCapability = getVisibleNavigation(true).flatMap((group) =>
      group.items.map((item) => item.path),
    )
    const withCapability = getVisibleNavigation(true, [
      'prompt_management',
    ]).flatMap((group) => group.items.map((item) => item.path))

    expect(withoutCapability).not.toContain('/admin/prompts')
    expect(withCapability).toContain('/admin/prompts')
  })

  it('registers the Prompt detail deep link under the same capability', () => {
    const routeObjects = [...pageRoutes, ...detailRoutes].map((route) => ({
      path: route.path,
    }))
    const match = matchRoutes(routeObjects, '/admin/prompts/support.reply')

    expect(match?.[0].params.promptId).toBe('support.reply')
    expect(detailRoutes.find((route) => route.path === '/admin/prompts/:promptId')).toMatchObject({
      pageId: 'ADM-PROMPT-DETAIL',
      parentPath: '/admin/prompts',
      entityIdParam: 'promptId',
      owner: '#807',
      capability: 'prompt_management',
    })
    expect(
      detailRoutes.find((route) => route.path === '/admin/prompts/:promptId'),
    ).toMatchObject({
      owner: '#807',
      capability: 'prompt_management',
      authOnly: true,
    })
  })
})
