import { describe, expect, it } from 'vitest'
import { reactive } from 'vue'

import { useProjectRouteScope } from './useProjectRouteScope'

describe('useProjectRouteScope', () => {
  it('returns one strict reactive project scope contract', () => {
    const route = reactive({
      path: '/projects/7/connections',
      params: { id: '7' },
    })
    const scope = useProjectRouteScope(route)

    expect(scope.isProjectRoute.value).toBe(true)
    expect(scope.routeProjectId.value).toBe(7)
    expect(scope.projectId.value).toBe(7)
    expect(scope.isValid.value).toBe(true)
    expect(scope.scopeKey.value).toBe('project:7')

    route.path = '/projects/8/tasks'
    route.params.id = '8'
    expect(scope.projectId.value).toBe(8)
    expect(scope.scopeKey.value).toBe('project:8')
  })

  it('never falls back to another project for invalid or global routes', () => {
    const invalid = useProjectRouteScope(
      reactive({ path: '/projects/7junk', params: { id: '7junk' } }),
    )
    expect(invalid.isProjectRoute.value).toBe(true)
    expect(invalid.routeProjectId.value).toBeNull()
    expect(Number.isNaN(invalid.projectId.value)).toBe(true)
    expect(invalid.isValid.value).toBe(false)
    expect(invalid.scopeKey.value).toBe('project:invalid')

    const global = useProjectRouteScope(
      reactive({ path: '/accounts', params: {} }),
    )
    expect(global.isProjectRoute.value).toBe(false)
    expect(global.routeProjectId.value).toBeNull()
    expect(global.scopeKey.value).toBe('/accounts')
  })

  it('classifies same-project query transitions separately from scope changes', () => {
    const scope = useProjectRouteScope(
      reactive({ path: '/projects/7/tasks', params: { id: '7' } }),
    )

    expect(
      scope.changesProjectScope({
        path: '/projects/7/tasks',
        params: { id: '7' },
      }),
    ).toBe(false)
    expect(
      scope.changesProjectScope({
        path: '/projects/8/tasks',
        params: { id: '8' },
      }),
    ).toBe(true)
    expect(
      scope.changesProjectScope({
        path: '/accounts',
        params: {},
      }),
    ).toBe(true)
  })
})
