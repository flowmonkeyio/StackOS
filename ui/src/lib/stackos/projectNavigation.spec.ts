import { describe, expect, it } from 'vitest'

import { projectSwitchDestination } from './projectNavigation'

describe('projectSwitchDestination', () => {
  it('keeps the current project surface and portable filters', () => {
    expect(
      projectSwitchDestination(
        {
          path: '/projects/1/tasks',
          query: { view: 'stories', status: 'in-progress', task: 'old-project-task' },
          hash: '#work-list',
        } as never,
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
        { path: '/projects/1/runs/162', query: {}, hash: '' } as never,
        7,
      ),
    ).toEqual({ path: '/projects/7/runs', query: {}, hash: '' })
  })

  it('keeps connection section while clearing the previous provider selection', () => {
    expect(
      projectSwitchDestination(
        {
          path: '/projects/1/connections',
          query: { section: 'services', provider_key: 'telegram' },
          hash: '',
        } as never,
        7,
      ),
    ).toEqual({
      path: '/projects/7/connections',
      query: { section: 'services' },
      hash: '',
    })
  })

  it('opens the selected project home when switching from the portfolio', () => {
    expect(
      projectSwitchDestination(
        { path: '/', query: { status: 'in-progress' }, hash: '#top' } as never,
        7,
      ),
    ).toEqual({ path: '/projects/7', query: {}, hash: '' })
  })
})
