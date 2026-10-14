import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import FinanceFilters from './FinanceFilters.vue'
import type { FinanceQuery } from '@/lib/finance'

const query: FinanceQuery = {
  start: '2026-09-01',
  end: '2026-09-30',
  recognition_basis: 'cash' as FinanceQuery['recognition_basis'],
  date_basis: 'transaction' as FinanceQuery['date_basis'],
  timezone: 'UTC',
  kind: 'all' as FinanceQuery['kind'],
  comparison: { start: '2026-08-01', end: '2026-08-31', clamped: false },
}

describe('finance filter synchronization', () => {
  it('preserves custom dates and search through typing and equivalent incoming props', async () => {
    const filters = mount(FinanceFilters, { props: { query } })
    await filters.get('[data-test="filter-start"]').setValue('2026-10-02')
    await filters.get('[data-test="filter-end"]').setValue('2026-10-29')
    await filters.get('[data-test="finance-search"]').setValue('unsubmitted search')
    await filters.setProps({ disabled: true })
    await filters.setProps({
      disabled: false,
      query: { ...query, comparison: { ...query.comparison! } },
    })
    expect((filters.get('[data-test="filter-start"]').element as HTMLInputElement).value).toBe(
      '2026-10-02',
    )
    expect((filters.get('[data-test="filter-end"]').element as HTMLInputElement).value).toBe(
      '2026-10-29',
    )
    expect(filters.text()).toContain('Custom dates')
    expect(filters.emitted('apply')).toBeUndefined()
    await filters.get('form').trigger('submit')
    expect(filters.emitted('apply')![0]![0]).toMatchObject({
      start: '2026-10-02',
      end: '2026-10-29',
      q: 'unsubmitted search',
      offset: 0,
    })
    filters.unmount()
  })

  it('synchronizes a changed applied query while preserving its reporting basis', async () => {
    const filters = mount(FinanceFilters, { props: { query } })
    await filters.get('[data-test="finance-search"]').setValue('unsubmitted search')
    await filters.setProps({
      query: {
        ...query,
        start: '2026-08-01',
        end: '2026-08-31',
        q: 'applied selection',
        recognition_basis: 'accrual' as FinanceQuery['recognition_basis'],
        account_ref: 'account:two',
      },
    })
    expect((filters.get('[data-test="filter-start"]').element as HTMLInputElement).value).toBe(
      '2026-08-01',
    )
    expect((filters.get('[data-test="finance-search"]').element as HTMLInputElement).value).toBe(
      'applied selection',
    )
    await filters.get('form').trigger('submit')
    expect(filters.emitted('apply')![0]![0]).toMatchObject({
      start: '2026-08-01',
      end: '2026-08-31',
      q: 'applied selection',
      recognition_basis: 'accrual',
      account_ref: 'account:two',
      timezone: 'UTC',
    })
    filters.unmount()
  })
})
