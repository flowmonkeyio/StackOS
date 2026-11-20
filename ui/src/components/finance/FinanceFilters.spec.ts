import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  DOMWrapper,
  enableAutoUnmount,
  flushPromises,
  mount,
  type VueWrapper,
} from '@vue/test-utils'
import { nextTick } from 'vue'
import FinanceFilters from './FinanceFilters.vue'
import { UiPopover, UiSelect } from '@/components/ui'
import { comparisonWindow, timeframe, type FinanceQuery } from '@/lib/finance'

const query: FinanceQuery = {
  start: '2026-09-01',
  end: '2026-09-30',
  recognition_basis: 'cash' as FinanceQuery['recognition_basis'],
  date_basis: 'transaction' as FinanceQuery['date_basis'],
  timezone: 'UTC',
  kind: 'all' as FinanceQuery['kind'],
  comparison: null,
}
const filterOptions = {
  accounts: [
    { value: 'bank-main', label: 'Operating account', aliases: ['bank:legacy'] },
    { value: 'unmapped:private', label: null, aliases: [] },
  ],
  counterparties: [
    { value: 'vendor:one', label: 'Example vendor' },
    { value: 'unnamed:private', label: null },
  ],
}
let available = 2000
let resize: () => void
enableAutoUnmount(afterEach)
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-10-14T12:00:00Z'))
  available = 2000
  vi.stubGlobal(
    'ResizeObserver',
    class {
      constructor(callback: () => void) {
        resize = callback
      }
      observe() {}
      disconnect() {}
    },
  )
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockImplementation(function (
    this: HTMLElement,
  ) {
    return this.classList.contains('ui-filter-chips__region') ? available : 0
  })
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(
    () => ({ width: 160 }) as DOMRect,
  )
})
afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})
function render(changes: Partial<FinanceQuery> = {}, extra = {}) {
  return mount(FinanceFilters, {
    attachTo: document.body,
    props: { query: { ...query, ...changes }, ...extra },
  })
}
function button(wrapper: ReturnType<typeof render>, label: string) {
  const result = wrapper.findAll('button').find((item) => item.text() === label)
  if (!result) throw new Error(`Missing button ${label}`)
  return result
}
async function edit(wrapper: ReturnType<typeof render>, label: string) {
  await button(wrapper, 'Filter').trigger('click')
  await button(wrapper, label).trigger('click')
}
function applied(wrapper: ReturnType<typeof render>): FinanceQuery {
  return wrapper.emitted('apply')!.at(-1)![0] as FinanceQuery
}
async function choose(select: VueWrapper, label: string) {
  await select.get('[role="combobox"]').trigger('click')
  const option = new DOMWrapper(document.body)
    .findAll('[role="option"]')
    .find((item) => item.text() === label)
  if (!option) throw new Error(`Missing option ${label}`)
  await option.trigger('click')
  await flushPromises()
}

describe('compact Finance filters', () => {
  it('keeps the closed toolbar compact with one period, filter picker and report options', async () => {
    const filters = render({}, { showSearch: false })
    expect(filters.get('[data-test="finance-period"]').text()).toBe('Last month')
    expect(filters.find('input').exists()).toBe(false)
    expect(filters.find('[role="dialog"]').exists()).toBe(false)
    expect(filters.text()).not.toMatch(/UTC|basis|reference|storage/)
    await button(filters, 'Filter').trigger('click')
    expect(filters.get('[role="dialog"]').text()).toContain('Filter by')
    expect(filters.find('input').exists()).toBe(false)
    await button(filters, 'Currency').trigger('click')
    expect(filters.findAll('input')).toHaveLength(1)
    expect(filters.get('[data-test="filter-currency"]').attributes('aria-describedby')).toBeTruthy()
  })

  it('period changes submit only intended dates and comparison, never a hidden field or search draft', async () => {
    const filters = render({ currency: 'EUR', q: 'committed' })
    await filters.get('[data-test="finance-search"]').setValue('unsubmitted search')
    await edit(filters, 'Currency')
    await filters.get('[data-test="filter-currency"]').setValue('usd')
    await filters.get('[data-test="finance-period"]').trigger('click')
    await choose(filters.findAllComponents(UiSelect)[0], 'This month')
    await choose(filters.findAllComponents(UiSelect)[1], 'Previous year')
    expect(filters.emitted('apply')).toBeUndefined()
    await filters.get('[data-test="apply-finance-period"]').trigger('click')
    expect(applied(filters)).toEqual({
      ...query,
      currency: 'EUR',
      q: 'committed',
      ...timeframe('month'),
      comparison: comparisonWindow('2026-10-01', '2026-10-31', 'year'),
      offset: 0,
    })
    expect(filters.find('[data-test="filter-currency"]').exists()).toBe(false)
    expect(filters.find('[aria-label="Edit Currency: EUR"]').exists()).toBe(true)
    expect(filters.find('[aria-label="Edit Currency: USD"]').exists()).toBe(false)
  })

  it('applied chips follow committed props and isolated edits discard on Escape', async () => {
    const filters = render({ currency: 'EUR' })
    await nextTick()
    const chip = filters.get('[aria-label="Edit Currency: EUR"]')
    await chip.trigger('click')
    await filters.get('[data-test="filter-currency"]').setValue('usd')
    expect(filters.find('[aria-label="Edit Currency: USD"]').exists()).toBe(false)
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await nextTick()
    expect(document.activeElement).toBe(chip.element)
    expect(filters.emitted('apply')).toBeUndefined()
    await chip.trigger('click')
    expect((filters.get('[data-test="filter-currency"]').element as HTMLInputElement).value).toBe(
      'EUR',
    )
    await filters.get('[data-test="filter-currency"]').setValue('usd')
    await filters.get('[data-test="apply-finance-filter"]').trigger('click')
    expect(applied(filters).currency).toBe('USD')
    expect(filters.find('[aria-label="Edit Currency: EUR"]').exists()).toBe(true)
    await filters.setProps({ query: applied(filters) })
    expect(filters.find('[aria-label="Edit Currency: EUR"]').exists()).toBe(false)
    expect(filters.find('[aria-label="Edit Currency: USD"]').exists()).toBe(true)
  })

  it('keeps invalid currency editable with an associated error and normalizes a correction', async () => {
    const filters = render()
    await edit(filters, 'Currency')
    const input = filters.get('[data-test="filter-currency"]')
    await input.setValue('12')
    await filters.get('[data-test="apply-finance-filter"]').trigger('click')
    expect(filters.emitted('apply')).toBeUndefined()
    expect(filters.get('[role="alert"]').text()).toContain('three-letter')
    expect(input.attributes('aria-invalid')).toBe('true')
    expect(input.attributes('aria-describedby')).toContain(
      filters.get('[role="alert"]').attributes('id'),
    )
    expect(filters.get('[data-test="apply-finance-filter"]').attributes('disabled')).toBeUndefined()
    await input.setValue('usd')
    await filters.get('[data-test="apply-finance-filter"]').trigger('click')
    expect(applied(filters).currency).toBe('USD')
  })

  it('validates dates and comparison before submission, and supports all time', async () => {
    const filters = render()
    await filters.get('[data-test="finance-period"]').trigger('click')
    await filters.get('[data-test="filter-start"]').setValue('2026-10-31')
    await filters.get('[data-test="filter-end"]').setValue('2026-10-01')
    await filters.get('[data-test="apply-finance-period"]').trigger('click')
    expect(filters.emitted('apply')).toBeUndefined()
    expect(filters.get('[role="alert"]').text()).toBe('Start must precede end.')
    await filters.get('[data-test="filter-start"]').setValue('')
    await filters.get('[data-test="apply-finance-period"]').trigger('click')
    expect(filters.get('[role="alert"]').text()).toBe('Choose valid dates.')
    await choose(filters.findAllComponents(UiSelect)[0], 'All time')
    expect(filters.findAllComponents(UiSelect)[1]!.props('disabled')).toBe(true)
    await filters.get('[data-test="apply-finance-period"]').trigger('click')
    expect(applied(filters)).toMatchObject({ ...timeframe('all'), comparison: null, offset: 0 })
  })

  it('isolates report options and validates the timezone while preserving other filters', async () => {
    const filters = render({ currency: 'EUR' })
    await button(filters, 'Report options').trigger('click')
    await choose(filters.findAllComponents(UiSelect)[0], 'When earned or incurred')
    await filters.get('[data-test="filter-timezone"]').setValue('Invalid/Zone')
    await filters.get('[data-test="apply-finance-filter"]').trigger('click')
    expect(filters.emitted('apply')).toBeUndefined()
    expect(filters.get('[role="alert"]').text()).toContain('valid timezone')
    await filters.get('[data-test="filter-timezone"]').setValue(' America/New_York ')
    await filters.get('[data-test="apply-finance-filter"]').trigger('click')
    expect(applied(filters)).toMatchObject({
      currency: 'EUR',
      recognition_basis: 'accrual',
      timezone: 'America/New_York',
      start: query.start,
      end: query.end,
    })
  })

  it('removes each clause intentionally and clears population filters while preserving period and report choices', async () => {
    const committed = {
      ...query,
      q: 'invoice',
      currency: 'EUR',
      needs_review: true,
      account_ref: 'bank:private',
      category_ref: 'category:private',
      counterparty_ref: 'party:private',
      external_project_ref: 'project:private',
      scope: 'external-project' as FinanceQuery['scope'],
      comparison: { start: '2025-09-01', end: '2025-09-30', clamped: false },
      timezone: 'America/New_York',
    }
    const filters = render(committed)
    await nextTick()
    expect(filters.text()).not.toMatch(
      /bank:private|category:private|party:private|project:private/,
    )
    await filters.get('[aria-label="Remove Selected account"]').trigger('click')
    expect(applied(filters)).toEqual({ ...committed, account_ref: null, offset: 0 })
    await filters.get('[aria-label="Remove Selected external project"]').trigger('click')
    expect(applied(filters)).toMatchObject({
      external_project_ref: null,
      scope: 'business',
      category_ref: 'category:private',
    })
    await button(filters, 'Clear filters').trigger('click')
    expect(applied(filters)).toEqual({
      ...committed,
      q: null,
      kind: 'all',
      currency: null,
      needs_review: false,
      account_ref: null,
      category_ref: null,
      counterparty_ref: null,
      external_project_ref: null,
      scope: 'business',
      offset: 0,
    })
  })

  it('uses authoritative named account/counterparty choices, aliases and deliberate raw inspection', async () => {
    const filters = render(
      { account_ref: 'bank:legacy', category_ref: 'category:private' },
      { filterOptions },
    )
    await nextTick()
    expect(filters.find('[aria-label="Edit Account: Operating account"]').exists()).toBe(true)
    await filters.get('[aria-label="Edit Account: Operating account"]').trigger('click')
    expect(filters.findComponent(UiSelect).props('modelValue')).toBe('bank-main')
    expect(filters.findComponent(UiSelect).props('options')).toEqual([
      { value: '', label: 'All accounts' },
      { value: 'bank-main', label: 'Operating account' },
    ])
    expect(filters.get('details code').text()).toBe('bank:legacy')
    await filters.get('[data-test="apply-finance-filter"]').trigger('click')
    expect(applied(filters).account_ref).toBe('bank-main')
    await edit(filters, 'Counterparty')
    await choose(filters.findComponent(UiSelect), 'Example vendor')
    await filters.get('[data-test="apply-finance-filter"]').trigger('click')
    expect(applied(filters).counterparty_ref).toBe('vendor:one')
    await filters.get('[aria-label="Edit Selected category"]').trigger('click')
    expect(filters.get('details code').text()).toBe('category:private')
    expect(filters.find('[data-test="apply-finance-filter"]').exists()).toBe(false)
  })

  it('search stays usable on Records and commits only after submission', async () => {
    const filters = render({ currency: 'EUR' })
    const input = filters.get('[data-test="finance-search"]')
    expect(input.attributes('type')).toBe('search')
    expect(input.attributes('id')).toBeTruthy()
    await input.setValue('invoice-42')
    expect(filters.emitted('apply')).toBeUndefined()
    await filters.get('form[aria-label="Search Finance records"]').trigger('submit')
    expect(applied(filters)).toEqual({ ...query, currency: 'EUR', q: 'invoice-42', offset: 0 })
    await filters.setProps({ showSearch: false, query: applied(filters) })
    expect(filters.find('[data-test="finance-search"]').exists()).toBe(false)
    expect(filters.find('[aria-label="Edit Search: invoice-42"]').exists()).toBe(true)
  })

  it('uses shared overflow anchoring and keeps portaled selects inside the chip editor', async () => {
    available = 80
    const filters = render({ currency: 'EUR', kind: 'expense' as FinanceQuery['kind'] })
    await nextTick()
    await button(filters, '+2 filters').trigger('click')
    const chip = filters.get('[aria-label="Edit Type: Expenses"]')
    await chip.trigger('click')
    const editor = filters
      .findAllComponents(UiPopover)
      .find((item) => item.props('ariaLabel') === 'Edit Finance filter')!
    expect(editor.props('anchor')).toBe(chip.element)
    const select = filters.findComponent(UiSelect)
    await select.get('[role="combobox"]').trigger('click')
    const option = new DOMWrapper(document.body)
      .findAll('[role="option"]')
      .find((item) => item.text() === 'Income')!
    await option.trigger('mousedown')
    await option.trigger('click')
    expect(editor.props('modelValue')).toBe(true)
    expect(filters.emitted('apply')).toBeUndefined()
    available = 2000
    resize()
    await flushPromises()
    const moved = filters.get('.ui-filter-chips__region [aria-label="Edit Type: Expenses"]')
    expect(editor.props('anchor')).toBe(moved.element)
    await filters.get('[data-test="apply-finance-filter"]').trigger('click')
    expect(applied(filters).kind).toBe('income')
    expect(document.activeElement).toBe(moved.element)
  })

  it('keeps edits on equivalent props and blocks submissions only while disabled', async () => {
    const filters = render()
    await edit(filters, 'Currency')
    await filters.get('[data-test="filter-currency"]').setValue('usd')
    await filters.setProps({ query: { ...query }, disabled: true })
    expect((filters.get('[data-test="filter-currency"]').element as HTMLInputElement).value).toBe(
      'usd',
    )
    expect(filters.get('[data-test="apply-finance-filter"]').attributes('disabled')).toBeDefined()
    await filters.setProps({ disabled: false })
    await filters.get('[data-test="apply-finance-filter"]').trigger('click')
    expect(applied(filters).currency).toBe('USD')
  })
})
