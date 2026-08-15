import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import ProjectSwitcher from './ProjectSwitcher.vue'
import type { Project } from '@/stores/projects'

const sample: Project[] = [
  {
    id: 1,
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

    const options = wrapper.findAll('[role="option"]')
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

    const listbox = wrapper.get('[role="listbox"]')
    expect(listbox.classes()).toContain('overflow-x-hidden')

    const option = wrapper.get('[role="option"]')
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
    expect(wrapper.findAll('[role="option"]')[1].attributes('aria-selected')).toBe('true')
  })

  it('emits one selection intent without mutating navigation state', async () => {
    const wrapper = renderSwitcher()
    await wrapper.find('button').trigger('click')
    await wrapper.findAll('[role="option"]')[1].trigger('click')

    expect(wrapper.emitted('select')).toEqual([[2]])
  })

  it('shows an honest empty state without a selected project', async () => {
    const wrapper = renderSwitcher([], null)
    expect(wrapper.find('button').text()).toContain('No project selected')

    await wrapper.find('button').trigger('click')
    expect(wrapper.text()).toContain('No projects yet.')
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
