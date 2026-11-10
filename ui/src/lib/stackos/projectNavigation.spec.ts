// @vitest-environment node

import { describe, expect, it } from 'vitest'

import { projectIdFromRoute, projectSwitchDestination } from './projectNavigation'

describe('projectIdFromRoute', () => {
  it('accepts complete positive integer ids only', () => {
    expect(projectIdFromRoute({ params: { id: '7' } })).toBe(7)
    expect(projectIdFromRoute({ params: { id: ['8'] } })).toBe(8)
    expect(projectIdFromRoute({ params: { id: '7junk' } })).toBeNull()
    expect(projectIdFromRoute({ params: { id: '0' } })).toBeNull()
    expect(projectIdFromRoute({ params: {} })).toBeNull()
  })
})

describe('projectSwitchDestination', () => {
  it.each([
    '',
    'schedules',
    'cost-budget',
    'agents',
    'library',
    'plugins',
    'connections',
    'tasks',
    'plugins/seo',
    'plugins/seo/workflows/seo.audit',
    'finance',
  ])('keeps the %s project surface', (surface) => {
    const suffix = surface ? `/${surface}` : ''

    expect(
      projectSwitchDestination(
        {
          path: `/projects/1${suffix}`,
          query: {},
          hash: '',
        },
        7,
      ),
    ).toEqual({
      path: `/projects/7${suffix}`,
      query: {},
      hash: '',
    })
  })

  it('keeps the current project surface and portable filters', () => {
    expect(
      projectSwitchDestination(
        {
          path: '/projects/1/tasks',
          query: { view: 'stories', status: 'in-progress', task: 'old-project-task' },
          hash: '#work-list',
        },
        7,
      ),
    ).toEqual({
      path: '/projects/7/tasks',
      query: { view: 'stories', status: 'in-progress' },
      hash: '#work-list',
    })
  })

  it('preserves a workflow identity across projects without carrying record selection', () => {
    expect(
      projectSwitchDestination(
        {
          path: '/projects/1/plugins/seo/workflows/seo.audit',
          query: { item: 'artifact:162' },
          hash: '',
        },
        7,
      ),
    ).toEqual({
      path: '/projects/7/plugins/seo/workflows/seo.audit',
      query: {},
      hash: '',
    })
  })

  it('keeps the connection section while clearing the previous provider selection', () => {
    expect(
      projectSwitchDestination(
        {
          path: '/projects/1/connections',
          query: { section: 'services', provider_key: 'telegram' },
          hash: '',
        },
        7,
      ),
    ).toEqual({
      path: '/projects/7/connections',
      query: { section: 'services' },
      hash: '',
    })
  })

  it('keeps Library filters while clearing the previous project record identity', () => {
    expect(
      projectSwitchDestination(
        {
          path: '/projects/1/library',
          query: { tab: 'saved', plugin: 'branding', type: 'decision', item: 'decision:42' },
          hash: '',
        },
        7,
      ),
    ).toEqual({
      path: '/projects/7/library',
      query: { tab: 'saved', plugin: 'branding', type: 'decision' },
      hash: '',
    })
  })

  it('opens the selected project home from the portfolio', () => {
    expect(
      projectSwitchDestination(
        {
          path: '/',
          query: { status: 'in-progress' },
          hash: '#top',
        },
        7,
      ),
    ).toEqual({ path: '/projects/7' })
  })

  it.each(['/accounts', '/setup'])('keeps the global %s surface in place', (path) => {
    expect(
      projectSwitchDestination(
        {
          path,
          query: { account: 'credential_123' },
          hash: '',
        },
        7,
      ),
    ).toBeNull()
  })
})
