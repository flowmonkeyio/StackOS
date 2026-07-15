import { beforeEach, describe, expect, it, vi } from 'vitest'

import { callOperation } from '@/lib/operations'

import { useHomePortfolioInsights } from './useHomePortfolioInsights'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))

const project = {
  id: 7,
  name: 'Customer portal',
  slug: 'customer-portal',
  domain: 'portal.test',
  is_active: true,
  updated_at: '2026-07-10T12:00:00Z',
}

describe('useHomePortfolioInsights', () => {
  beforeEach(() => vi.mocked(callOperation).mockReset())

  it('aggregates project health and active work into portfolio supervision data', async () => {
    vi.mocked(callOperation).mockImplementation(async (operation) => {
      if (operation === 'tracker.status') {
        return {
          task_counts: {
            'not-started': 1,
            'in-progress': 1,
            complete: 8,
          },
          ticket_counts: { 'in-progress': 2 },
          in_progress_ticket_count: 2,
          blocked_ticket_count: 1,
        } as never
      }
      return {
        tasks: [
          {
            key: 'ship-onboarding',
            title: 'Ship onboarding improvements',
            owner: 'codex',
            status: 'in-progress',
            priority_key: 'p0',
            updated_at: '2026-07-10T12:00:00Z',
            ticket_summary: {
              total_count: 3,
              terminal_count: 1,
              in_progress_count: 2,
              blocked_count: 1,
            },
          },
          {
            key: 'plan-release',
            title: 'Plan release',
            owner: null,
            status: 'not-started',
            priority_key: 'p1',
            updated_at: '2026-07-09T12:00:00Z',
            ticket_summary: {
              total_count: 0,
              terminal_count: 0,
              in_progress_count: 0,
              blocked_count: 0,
            },
          },
        ],
        tickets: [],
      } as never
    })

    const portfolio = useHomePortfolioInsights()
    await portfolio.load([project as never])

    expect(portfolio.totals.value).toEqual({
      activeProjects: 1,
      activeTasks: 2,
      activeTickets: 2,
      blockers: 1,
    })
    expect(portfolio.insights.value[0]).toMatchObject({
      projectId: 7,
      completionPercent: 80,
      blockedTicketCount: 1,
    })
    expect(portfolio.activeWork.value[0]).toMatchObject({
      title: 'Ship onboarding improvements',
      projectName: 'Customer portal',
      openTicketCount: 2,
      blockerCount: 1,
    })
    expect(callOperation).toHaveBeenCalledWith(
      'tracker.get',
      expect.objectContaining({
        project_id: 7,
        task_index_only: true,
        task_statuses: ['not-started', 'in-progress'],
        include_graph: false,
      }),
    )
    expect(callOperation).toHaveBeenCalledWith(
      'tracker.status',
      expect.objectContaining({ project_id: 7, response_mode: 'compact' }),
    )
  })

  it('does not fetch task detail for projects with no active work', async () => {
    vi.mocked(callOperation).mockResolvedValue({
      task_counts: { complete: 4 },
      ticket_counts: { complete: 9 },
      in_progress_ticket_count: 0,
      blocked_ticket_count: 0,
    } as never)

    const portfolio = useHomePortfolioInsights()
    await portfolio.load([project as never])

    expect(callOperation).toHaveBeenCalledTimes(1)
    expect(callOperation).toHaveBeenCalledWith(
      'tracker.status',
      expect.objectContaining({ project_id: 7 }),
    )
    expect(portfolio.activeWork.value).toEqual([])
  })

  it('publishes project insights as they arrive instead of waiting for the slowest project', async () => {
    let resolveSlowProject!: (value: unknown) => void
    const slowProject = new Promise((resolve) => {
      resolveSlowProject = resolve
    })
    vi.mocked(callOperation).mockImplementation(async (_operation, argumentsJson) => {
      if (argumentsJson?.project_id === 8) return (await slowProject) as never
      return {
        summary: { tasks: { total: 4, active: 0, done: 4 }, tickets: {} },
        task_counts: { complete: 4 },
      } as never
    })

    const portfolio = useHomePortfolioInsights()
    const loading = portfolio.load([
      project as never,
      { ...project, id: 8, name: 'Slow project' } as never,
    ])

    await vi.waitFor(() =>
      expect(portfolio.insights.value.map((item) => item.projectId)).toEqual([7]),
    )
    expect(portfolio.loading.value).toBe(true)

    resolveSlowProject({
      summary: { tasks: { total: 2, active: 0, done: 2 }, tickets: {} },
      task_counts: { complete: 2 },
    })
    await loading
    expect(portfolio.insights.value.map((item) => item.projectId)).toEqual([7, 8])
  })
})
