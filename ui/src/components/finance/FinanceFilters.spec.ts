import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import FinanceFilters from './FinanceFilters.vue'
import { comparisonWindow, timeframe, type FinanceQuery } from '@/lib/finance'

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
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-10-14T12:00:00Z'))
  })
  afterEach(() => vi.useRealTimers())
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

  it.each([
    ['week', '2026-10-12', '2026-10-18'],
    ['month', '2026-10-01', '2026-10-31'],
    ['quarter', '2026-10-01', '2026-12-31'],
    ['year', '2026-01-01', '2026-12-31'],
  ])(
    'applies %s in one click and preserves the selected population and comparison',
    async (period, start, end) => {
      const selected: FinanceQuery = {
        ...query,
        q: 'invoice 17',
        kind: 'expense' as FinanceQuery['kind'],
        recognition_basis: 'accrual' as FinanceQuery['recognition_basis'],
        date_basis: 'posting' as FinanceQuery['date_basis'],
        timezone: 'America/New_York',
        scope: 'external-project' as FinanceQuery['scope'],
        external_project_ref: 'project:one',
        account_ref: 'bank:one',
        category_ref: 'category:one',
        counterparty_ref: 'vendor:one',
        currency: 'USD',
        needs_review: true,
        offset: 100,
        limit: 25,
        sort: 'amount' as FinanceQuery['sort'],
        descending: true,
        comparison: comparisonWindow(query.start, query.end, 'year'),
      }
      const before = structuredClone(selected)
      const filters = mount(FinanceFilters, { props: { query: selected } })
      expect(filters.emitted('apply')).toBeUndefined()
      await filters.get(`[data-test="finance-period-${period}"]`).trigger('click')
      expect(filters.emitted('apply')).toEqual([
        [{ ...selected, start, end, offset: 0, comparison: comparisonWindow(start, end, 'year') }],
      ])
      expect(selected).toEqual(before)
      expect(filters.get(`[data-test="finance-period-${period}"]`).attributes('aria-pressed')).toBe(
        'true',
      )
      expect(filters.text()).toContain('Calendar shortcuts use UTC dates.')
      filters.unmount()
    },
  )

  it('matches buttons and selector to draft edits and changed parent dates without applying', async () => {
    const filters = mount(FinanceFilters, { props: { query: { ...query, ...timeframe('year') } } })
    expect(filters.text()).toContain('Calendar year')
    expect(filters.get('[data-test="finance-period-year"]').attributes('aria-pressed')).toBe('true')
    await filters.get('[data-test="filter-start"]').setValue('2026-10-02')
    expect(filters.text()).toContain('Custom dates')
    expect(filters.findAll('[aria-pressed="true"]')).toHaveLength(0)
    await filters.setProps({ query: { ...query, ...timeframe('month') } })
    expect(filters.text()).toContain('This month')
    expect(filters.get('[data-test="finance-period-month"]').attributes('aria-pressed')).toBe(
      'true',
    )
    await filters.get('[data-test="filter-start"]').setValue('2026-10-02')
    await filters.get('[data-test="filter-start"]').setValue('2026-10-01')
    expect(filters.text()).toContain('This month')
    expect(filters.get('[data-test="finance-period-month"]').attributes('aria-pressed')).toBe(
      'true',
    )
    expect(filters.emitted('apply')).toBeUndefined()
    filters.unmount()
  })

  it('blocks every shortcut and form submission while loading', async () => {
    const filters = mount(FinanceFilters, { props: { query, disabled: true } })
    for (const period of ['week', 'month', 'quarter', 'year']) {
      const button = filters.get(`[data-test="finance-period-${period}"]`)
      expect(button.attributes('disabled')).toBeDefined()
      await button.trigger('click')
    }
    await filters.get('form').trigger('submit')
    expect(filters.emitted('apply')).toBeUndefined()
    expect((filters.get('[data-test="filter-start"]').element as HTMLInputElement).value).toBe(
      query.start,
    )
    filters.unmount()
  })

  it('keeps Year to date, Last month and All time separate with explicit apply', async () => {
    const filters = mount(FinanceFilters, {
      props: { query: { ...query, ...timeframe('year'), comparison: null } },
    })
    async function choose(label: string) {
      await filters.findAll('[role="combobox"]')[0]!.trigger('click')
      await filters
        .findAll('[role="option"]')
        .find((option) => option.text() === label)!
        .trigger('click')
    }
    await choose('Year to date')
    expect(filters.emitted('apply')).toBeUndefined()
    await filters.get('form').trigger('submit')
    expect(filters.emitted('apply')!.at(-1)![0]).toMatchObject({
      start: '2026-01-01',
      end: '2026-10-14',
    })
    await choose('Last month')
    await filters.get('form').trigger('submit')
    expect(filters.emitted('apply')!.at(-1)![0]).toMatchObject({
      start: '2026-09-01',
      end: '2026-09-30',
    })
    await choose('All time')
    expect(filters.findAll('[role="combobox"]')[1]!.attributes('disabled')).toBeDefined()
    await filters.get('form').trigger('submit')
    expect(filters.emitted('apply')!.at(-1)![0]).toMatchObject({
      start: '0001-01-01',
      end: '9999-12-31',
      comparison: null,
    })
    await filters.get('[data-test="finance-period-year"]').trigger('click')
    expect(filters.emitted('apply')!.at(-1)![0]).toMatchObject({
      start: '2026-01-01',
      end: '2026-12-31',
      comparison: null,
    })
    filters.unmount()
  })

  it.each(['period', 'year'])(
    'retains parent %s comparison mode across a changed query and shortcut',
    async (mode) => {
      const filters = mount(FinanceFilters, { props: { query: { ...query, comparison: null } } })
      const changed = {
        ...query,
        ...timeframe('month'),
        comparison: comparisonWindow('2026-10-01', '2026-10-31', mode),
      }
      await filters.setProps({ query: changed })
      await filters.get('[data-test="finance-period-week"]').trigger('click')
      expect(filters.emitted('apply')![0]![0]).toMatchObject({
        comparison: comparisonWindow('2026-10-12', '2026-10-18', mode),
      })
      filters.unmount()
    },
  )

  it('rejects reversed and invalid custom dates without a request', async () => {
    const filters = mount(FinanceFilters, { props: { query } })
    await filters.get('[data-test="filter-start"]').setValue('2026-10-20')
    await filters.get('[data-test="filter-end"]').setValue('2026-10-01')
    await filters.get('form').trigger('submit')
    expect(filters.get('[role="alert"]').text()).toContain('Start must precede end')
    await filters.get('[data-test="filter-start"]').setValue('2026-02-30')
    await filters.get('form').trigger('submit')
    expect(filters.get('[role="alert"]').text()).toContain('Choose valid dates')
    expect(filters.emitted('apply')).toBeUndefined()
    filters.unmount()
  })
})
