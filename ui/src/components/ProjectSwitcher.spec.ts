import { afterEach, describe, expect, it } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'

import ProjectSwitcher from './ProjectSwitcher.vue'
import type { Project } from '@/stores/projects'
import UiReorderList from '@/components/ui/UiReorderList.vue'

enableAutoUnmount(afterEach)

const sample: Project[] = [
  {
    id: 1,
    order_index: 1,
    name: 'Alpha',
    slug: 'alpha',
    domain: 'alpha.test',
    niche: 'a',
    locale: 'en-US',
    is_active: true,
    schedule_json: null,
    created_at: '2025-01-01T00:00:00Z',
    updated_at: '2025-01-01T00:00:00Z',
  },
  {
    id: 2,
    order_index: 2,
    name: 'Beta',
    slug: 'beta',
    domain: 'beta.test',
    niche: 'b',
    locale: 'en-US',
    is_active: false,
    schedule_json: null,
    created_at: '2025-01-01T00:00:00Z',
    updated_at: '2025-01-01T00:00:00Z',
  },
]

describe('ProjectSwitcher', () => {
  it('shows the selected project name in the collapsed button', () => {
    const wrapper = renderSwitcher()
    expect(wrapper.find('button').text()).toContain('Alpha')
  })

  it('opens the dropdown and lists live projects before archived projects', async () => {
    const wrapper = renderSwitcher([sample[1], sample[0]], sample[0])
    await wrapper.find('button').trigger('click')

    const options = wrapper.findAll('[data-project-select]')
    expect(options).toHaveLength(2)
    expect(options[0].text()).toContain('Alpha')
    expect(options[1].text()).toContain('Beta')
  })

  it('clips long project rows instead of allowing horizontal menu overflow', async () => {
    const project = {
      ...sample[0],
      name: 'Project with an extremely long operator-facing name that should truncate',
      slug: 'project-with-an-extremely-long-slug-that-should-truncate',
      domain: 'very-long-project-domain-name-that-should-not-stretch-the-dropdown.local',
    }
    const wrapper = renderSwitcher([project], project)

    await wrapper.find('button').trigger('click')

    const listbox = wrapper.getComponent(UiReorderList)
    expect(listbox.classes()).toContain('overflow-x-hidden')

    const option = wrapper.get('[data-project-select]')
    expect(option.classes()).toContain('min-w-0')
    expect(option.classes()).toContain('overflow-hidden')

    const textColumn = option.get('span')
    expect(textColumn.classes()).toContain('min-w-0')
    expect(textColumn.classes()).toContain('flex-1')
    expect(textColumn.findAll('span').every((row) => row.classes().includes('truncate'))).toBe(true)
  })

  it('renders the selected project supplied by the composition owner', async () => {
    const wrapper = renderSwitcher(sample, sample[1])

    expect(wrapper.find('button').text()).toContain('Beta')
    await wrapper.find('button').trigger('click')
    expect(wrapper.findAll('[data-project-select]')[1].attributes('aria-current')).toBe('true')
  })

  it('emits one selection intent without mutating navigation state', async () => {
    const wrapper = renderSwitcher()
    await wrapper.find('button').trigger('click')
    await wrapper.findAll('[data-project-select]')[1].trigger('click')

    expect(wrapper.emitted('select')).toEqual([[2]])
  })

  it('shows an honest empty state without a selected project', async () => {
    const wrapper = renderSwitcher([], null)
    expect(wrapper.find('button').text()).toContain('No project selected')

    await wrapper.find('button').trigger('click')
    expect(wrapper.text()).toContain('No projects yet.')
  })

  it('retains the closed geometry and widens only the opened, clamped menu', async () => {
    const wrapper = renderSwitcher()
    const trigger = wrapper.get('button')
    expect(trigger.classes().join(' ')).toBe('focus-ring-sb flex w-full min-w-0 items-center gap-2.5 rounded-md border border-sb-border bg-sb-hover px-2.5 py-2 text-left text-sm transition-colors duration-fast hover:bg-sb-active')
    await trigger.trigger('click')
    const menu = wrapper.get('[aria-label="Projects"]')
    expect(menu.classes()).toContain('w-[360px]')
    expect(menu.classes()).toContain('left-0')
    expect(trigger.attributes('aria-controls')).toBe(menu.attributes('id'))
    expect(menu.attributes('style')).toContain('max-width:')
    expect(wrapper.getComponent(UiReorderList).classes()).toContain('overflow-y-auto')
    expect(wrapper.emitted('opened')).toEqual([[]])
  })

  it('uses the admitted handle and ellipsis alternatives without selecting or mutating at boundaries', async () => {
    const live = [sample[0], { ...sample[1], is_active: true }]
    const wrapper = renderSwitcher(live)
    await wrapper.setProps({ orderReady: true })
    await wrapper.get('button').trigger('click')
    const handles = wrapper.findAll('[data-reorder-handle]')
    expect(handles).toHaveLength(2)
    expect(handles[0].findAll('svg')).toHaveLength(2)
    expect(wrapper.findAll('[draggable="true"]')).toHaveLength(2)
    await handles[0].trigger('click')
    expect(wrapper.get('[role="menu"]').element.closest('[aria-label="Projects"]')).toBe(wrapper.get('[aria-label="Projects"]').element)
    expect(wrapper.get('[role="menu"]').element.closest('.ui-reorder-list')).toBeNull()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    expect(wrapper.emitted('move')).toBeUndefined()
    await wrapper.get('button[aria-label="Actions for Alpha"]').trigger('click')
    const menu = wrapper.get('[role="menu"]')
    expect(menu.findAll('[role="menuitem"]').map((item) => item.text())).toEqual(['Rename project…', 'Move to top', 'Move up', 'Move down', 'Delete project…'])
    expect(menu.findAll('button')[1].attributes('disabled')).toBeDefined()
    expect(menu.findAll('button')[2].attributes('disabled')).toBeDefined()
    await menu.findAll('button')[3].trigger('click')
    expect(wrapper.emitted('move')).toEqual([[{ itemKey: '1', anchorKey: '2', placement: 'after' }]])
    expect(wrapper.emitted('select')).toBeUndefined()
    await wrapper.get('button[aria-label="Actions for Alpha"]').trigger('click')
    await wrapper.get('[role="menu"]').findAll('button')[0].trigger('click')
    expect(wrapper.emitted('rename')).toEqual([[1]])
    await wrapper.get('button[aria-label="Actions for Beta"]').trigger('click')
    await wrapper.get('[role="menu"]').findAll('button').at(-1)!.trigger('click')
    expect(wrapper.emitted('delete')).toEqual([[2]])
    expect(wrapper.emitted('select')).toBeUndefined()
  })

  it('keeps movement disabled while incomplete or pending, and Escape cancels only the row menu first', async () => {
    const wrapper = renderSwitcher()
    await wrapper.get('button').trigger('click')
    expect(wrapper.get('[data-reorder-handle]').attributes('disabled')).toBeDefined()
    await wrapper.get('button[aria-label="Actions for Alpha"]').trigger('click')
    await wrapper.get('[role="menu"]').trigger('keydown', { key: 'Escape' })
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    expect(wrapper.find('[role="menu"]').exists()).toBe(false)
    expect(wrapper.find('[aria-label="Projects"]').exists()).toBe(true)
    await wrapper.get('button').trigger('keydown', { key: 'Escape' })
    expect(wrapper.find('[aria-label="Projects"]').exists()).toBe(false)
    expect(wrapper.emitted('move')).toBeUndefined()
  })
})

function renderSwitcher(
  items = sample,
  selectedProject: Project | null = sample[0],
) {
  return mount(ProjectSwitcher, {
    props: { items, selectedProject },
  })
}
