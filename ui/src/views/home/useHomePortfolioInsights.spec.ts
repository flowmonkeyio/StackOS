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
            priority_key: 'p0',
            updated_at: '2026-07-10T12:00:00Z',
          },
        ],
        tickets: [
          { task_key: 'ship-onboarding', blocked_by: [], key: 'copy' },
          { task_key: 'ship-onboarding', blocked_by: ['approval'], key: 'release' },
        ],
      } as never
    })

    const portfolio = useHomePortfolioInsights()
    await portfolio.load([project as never])

    expect(portfolio.totals.value).toEqual({
      activeProjects: 1,
      activeTasks: 1,
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
      activeTicketCount: 2,
      blockerCount: 1,
    })
    expect(callOperation).toHaveBeenCalledWith(
      'tracker.get',
      expect.objectContaining({ project_id: 7, statuses: ['in-progress'] }),
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
})
