import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import TrackerBoardFilterBar from './TrackerBoardFilterBar.vue'
import { parseBoardFilters } from './boardFilters'

let available = 480
let resize: () => void
const disconnect = vi.fn()
beforeEach(() => {
  available = 480
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback: () => void) { resize = callback }
    observe() {}
    disconnect = disconnect
  })
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockImplementation(function (this: HTMLElement) {
    return this.classList.contains('board-filter-bar__chips') ? available : 0
  })
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(() => ({ width: 160 } as DOMRect))
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); disconnect.mockClear() })
function render(query = {}) {
  return mount(TrackerBoardFilterBar, { props: {
    filters: parseBoardFilters(query), workflowOptions: [{ value: 'engineering', label: 'Tracked delivery' }],
    assigneeOptions: [{ value: 'Codex Main', label: 'Codex Main' }], taskRowsCount: 3, tasksCount: 12,
  } })
}
function button(wrapper: ReturnType<typeof render>, text: string) {
  const found = wrapper.findAll('button').find(item => item.text() === text)
  if (!found) throw new Error(`Missing button ${text}`)
  return found
}

describe('Board filter controls', () => {
  it('uses Text as a filter, edits the existing clause and removes blank text', async () => {
    const wrapper = render()
    expect(wrapper.find('input[type="search"]').exists()).toBe(false)
    await button(wrapper, 'Filter').trigger('click')
    await button(wrapper, 'Text').trigger('click')
    await wrapper.get('input[type="search"]').setValue('server')
    await wrapper.get('form').trigger('submit')
    expect(wrapper.emitted('update:filter')?.[0]).toEqual([{ field: 'text', value: 'server' }])
    await wrapper.setProps({ filters: parseBoardFilters({ search: 'server', status: 'all', completed: 'all' }) })
    await wrapper.get('[aria-label="Edit Text: server"]').trigger('click')
    expect((wrapper.get('input').element as HTMLInputElement).value).toBe('server')
    await wrapper.get('input').setValue('')
    await wrapper.get('form').trigger('submit')
    expect(wrapper.emitted('update:filter')?.[1]).toEqual([{ field: 'text', value: '' }])
    wrapper.unmount()
  })

  it('supports limited multi-value status/priority and single-value workflow/assignee operators', async () => {
    const wrapper = render()
    await button(wrapper, 'Filter').trigger('click')
    await button(wrapper, 'Status').trigger('click')
    expect(wrapper.findAll('input[type="checkbox"]')).toHaveLength(7)
    await wrapper.get('input[type="checkbox"]').setValue(true)
    await wrapper.get('form').trigger('submit')
    expect(wrapper.emitted('update:filter')?.[0]).toEqual([{ field: 'status', operator: 'is-not', values: ['aborted', 'not-started'] }])
    await button(wrapper, 'Filter').trigger('click')
    await button(wrapper, 'Priority').trigger('click')
    expect(wrapper.findAll('input[type="checkbox"]')).toHaveLength(4)
    await wrapper.get('input[type="checkbox"]').setValue(true)
    await wrapper.get('form').trigger('submit')
    expect(wrapper.emitted('update:filter')?.[1]).toEqual([{ field: 'priority', operator: 'is', values: ['p0'] }])
    for (const [field, label, value] of [['workflow', 'Workflow', 'engineering'], ['assignee', 'Assignee', 'Codex Main']] as const) {
      await button(wrapper, 'Filter').trigger('click')
      await button(wrapper, label).trigger('click')
      const selects = wrapper.findAllComponents({ name: 'UiSelect' })
      selects[0]!.vm.$emit('update:modelValue', 'is-not')
      selects[1]!.vm.$emit('update:modelValue', value)
      await wrapper.get('form').trigger('submit')
      expect(wrapper.emitted('update:filter')?.at(-1)).toEqual([{ field, operator: 'is-not', value }])
    }
    wrapper.unmount()
  })

  it('collapses clauses on resize and uses the same editor/removal commands in overflow', async () => {
    const wrapper = render({ search: 'a very long filter value', priority: 'p1', workflow: 'engineering', ticket_status: 'complete', focus: 'blocked' })
    await nextTick()
    expect(wrapper.findAll('.board-filter-bar__chips > .board-filter-bar__chip')).toHaveLength(2)
    expect(button(wrapper, '+5 filters').exists()).toBe(true)
    available = 80; resize(); await nextTick()
    expect(wrapper.findAll('.board-filter-bar__chips > .board-filter-bar__chip')).toHaveLength(0)
    await button(wrapper, '+7 filters').trigger('click')
    await wrapper.get('[aria-label="Remove Has blocked tickets"]').trigger('click')
    expect(wrapper.emitted('removeFilter')?.[0]).toEqual(['blocked'])
    await wrapper.get('[aria-label="Edit Workflow is: engineering"]').trigger('click')
    expect(wrapper.find('[aria-label="More filters"]').exists()).toBe(false)
    expect(wrapper.get('[aria-label="Workflow value"]').text()).toContain('Tracked delivery')
    available = 2000; resize(); await nextTick()
    expect(wrapper.findAll('.board-filter-bar__chips > .board-filter-bar__chip')).toHaveLength(7)
    wrapper.unmount()
    expect(disconnect).toHaveBeenCalledOnce()
  })

  it('exposes exact completion choices, hide-empty and clear including the default completion chip', async () => {
    const wrapper = render()
    await nextTick()
    await wrapper.get('[aria-label="Edit Completed: Past week"]').trigger('click')
    const select = wrapper.findComponent({ name: 'UiSelect' })
    expect(select.props('options')).toEqual([
      { value: 'all', label: 'All' }, { value: 'past-day', label: 'Past day' }, { value: 'past-week', label: 'Past week' },
      { value: 'past-month', label: 'Past month' }, { value: 'none', label: 'None' },
    ])
    select.vm.$emit('change', 'none')
    expect(wrapper.emitted('update:display')?.[0]).toEqual([{ completed: 'none' }])
    await wrapper.get('[aria-label="Hide empty columns"]').trigger('click')
    expect(wrapper.emitted('update:display')?.[1]).toEqual([{ hideEmptyColumns: true }])
    await wrapper.get('[aria-label="Remove Completed: Past week"]').trigger('click')
    expect(wrapper.emitted('update:display')?.[2]).toEqual([{ completed: 'all' }])
    await button(wrapper, 'Clear all').trigger('click')
    expect(wrapper.emitted('clear')).toHaveLength(1)
    await wrapper.setProps({ filters: { clauses: [], completed: 'all', hideEmptyColumns: true } })
    expect(button(wrapper, 'Clear all').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[role="status"]').text()).toBe('3 / 12 tasks')
    await flushPromises()
    wrapper.unmount()
  })
})
