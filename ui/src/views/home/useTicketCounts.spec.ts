import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { callOperation } from '@/lib/operations'
import { useTicketCounts } from './useTicketCounts'
import { ticketSummaryFixture } from './overviewFixtures'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))
beforeEach(() => {
  vi.mocked(callOperation).mockReset()
})

describe('ticket count scope lifecycle', () => {
  it('ignores an old visibility response and keeps last-good counts on refresh failure', async () => {
    let counts!: ReturnType<typeof useTicketCounts>
    const wrapper = mount({
      setup() {
        counts = useTicketCounts()
        return () => null
      },
    })
    let finishOld!: (value: unknown) => void
    vi.mocked(callOperation).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishOld = resolve
        }),
    )
    const old = counts.load()
    const all = ticketSummaryFixture({ is_active: null })
    vi.mocked(callOperation).mockResolvedValue(all)
    await counts.setVisibility(null)
    finishOld(ticketSummaryFixture())
    await old
    expect(counts.summary.value?.is_active).toBeNull()
    expect(callOperation).toHaveBeenLastCalledWith('tracker.ticketCountsAll', { is_active: null })
    vi.mocked(callOperation).mockRejectedValue(new Error('offline'))
    await counts.load()
    expect(counts.summary.value).toEqual(all)
    expect(counts.error.value).toBe('offline')
    wrapper.unmount()
  })
})
