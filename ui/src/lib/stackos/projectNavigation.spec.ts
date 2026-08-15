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
    'inbox',
    'activity',
    'setup',
    'schedules',
    'cost-budget',
    'plugins',
    'capabilities',
    'connections',
    'operations',
    'action-calls',
    'agent-requests',
    'agent-presets',
    'tasks',
    'workflow-templates',
    'data',
    'resources',
    'runs',
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

  it('returns to the project runs list from a project-specific run detail', () => {
    expect(
      projectSwitchDestination(
        {
          path: '/projects/1/runs/162',
          query: {},
          hash: '',
        },
        7,
      ),
    ).toEqual({
      path: '/projects/7/runs',
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

  it('keeps resource filters while clearing a resource selected in the old project', () => {
    expect(
      projectSwitchDestination(
        {
          path: '/projects/1/resources',
          query: { plugin_slug: 'branding', resource_key: 'old-resource' },
          hash: '',
        },
        7,
      ),
    ).toEqual({
      path: '/projects/7/resources',
      query: { plugin_slug: 'branding' },
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

  it('keeps a global operating surface in place', () => {
    expect(
      projectSwitchDestination(
        {
          path: '/accounts',
          query: { account: 'credential_123' },
          hash: '',
        },
        7,
      ),
    ).toBeNull()
  })
})
