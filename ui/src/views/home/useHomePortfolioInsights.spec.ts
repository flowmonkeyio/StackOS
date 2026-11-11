// @vitest-environment node

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { callOperation } from '@/lib/operations'
import { useHomePortfolioInsights } from './useHomePortfolioInsights'
import { useProjectsStore } from '@/stores/projects'
import { portfolioFixture } from './overviewFixtures'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))
const project = {
  id: 7,
  name: 'Portal',
  latest_task: { key: 'release', title: 'Release', status: 'complete' },
  ticket_count: 2,
}

describe('useHomePortfolioInsights', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.mocked(callOperation).mockReset()
  })

  it('reads the canonical paginated portfolio including completed work', async () => {
    vi.mocked(callOperation).mockResolvedValue({
      items: [project],
      next_cursor: 7,
      total_estimate: 2,
    })
    const portfolio = useHomePortfolioInsights()
    await portfolio.load()
    expect(callOperation).toHaveBeenCalledWith(
      'project.portfolio',
      expect.objectContaining({ is_active: true, sort: 'recent', limit: 50 }),
    )
    expect(portfolio.items.value[0]?.latest_task?.status).toBe('complete')
    expect(portfolio.nextCursor.value).toBe(7)
    vi.mocked(callOperation).mockResolvedValue({
      items: [{ ...project, id: 8 }],
      next_cursor: null,
      total_estimate: 2,
    })
    await portfolio.loadMore()
    expect(callOperation).toHaveBeenLastCalledWith(
      'project.portfolio',
      expect.objectContaining({ after_id: 7 }),
    )
    expect(portfolio.items.value.map((item) => item.id)).toEqual([7, 8])
  })

  it('ignores an older request after the filter changes', async () => {
    const projects = useProjectsStore()
    const cached = { ...portfolioFixture({ id: 7, name: 'Current cached name' }), order_index: 7, locale: 'en-US', niche: null, schedule_json: null }
    projects.items = [cached]
    let oldReply!: (value: unknown) => void
    vi.mocked(callOperation).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          oldReply = resolve
        }),
    )
    const portfolio = useHomePortfolioInsights()
    const old = portfolio.load()
    vi.mocked(callOperation).mockResolvedValue({
      items: [{ ...project, id: 9 }],
      next_cursor: null,
    })
    await portfolio.setFilters({
      query: 'new',
      is_active: null,
      sort: 'name',
      ticket_status: 'complete',
    })
    oldReply({ items: [{ ...cached, name: 'Obsolete filtered result', updated_at: '2026-09-08T00:00:00Z' }], next_cursor: 7 })
    await old
    expect(portfolio.items.value.map((item) => item.id)).toEqual([9])
    expect(projects.getById(7)?.name).toBe('Current cached name')
  })

  it('keeps last-good data and an explicit error when background refresh fails', async () => {
    vi.mocked(callOperation).mockResolvedValue({ items: [project], next_cursor: null })
    const portfolio = useHomePortfolioInsights()
    await portfolio.load()
    vi.mocked(callOperation).mockRejectedValue(new Error('offline'))
    await portfolio.load()
    expect(portfolio.items.value).toHaveLength(1)
    expect(portfolio.error.value).toBeTruthy()
  })
})
