import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import SubNav from './SubNav.vue'

const groups = [
  {
    items: [
      { key: 'overview', label: 'Overview', icon: 'gauge' },
      { key: 'services', label: 'Services', icon: 'plug', count: 3 },
    ],
  },
  {
    label: 'Messaging',
    items: [
      { key: 'bots', label: 'Bots', icon: 'chat', count: 1 },
      { key: 'channels', label: 'Channels', icon: 'megaphone' },
    ],
  },
]

describe('SubNav', () => {
  it('renders grouped tabs with a group label and count badges', () => {
    const wrapper = mount(SubNav, { props: { groups, activeKey: 'overview' } })
    expect(wrapper.findAll('[role="tab"]')).toHaveLength(4)
    expect(wrapper.text()).toContain('Messaging')
    expect(wrapper.text()).toContain('Services')
    expect(wrapper.text()).toContain('3')
  })

  it('marks the active tab as selected and focusable', () => {
    const wrapper = mount(SubNav, { props: { groups, activeKey: 'bots' } })
    const active = wrapper
      .findAll('[role="tab"]')
      .find((tab) => tab.attributes('aria-selected') === 'true')
    expect(active?.text()).toContain('Bots')
    expect(active?.attributes('tabindex')).toBe('0')
  })

  it('emits change when a different tab is clicked', async () => {
    const wrapper = mount(SubNav, { props: { groups, activeKey: 'overview' } })
    const services = wrapper.findAll('[role="tab"]').find((tab) => tab.text().includes('Services'))
    await services?.trigger('click')
    expect(wrapper.emitted('change')?.[0]).toEqual(['services'])
  })

  it('does not emit change when the active tab is clicked again', async () => {
    const wrapper = mount(SubNav, { props: { groups, activeKey: 'overview' } })
    const overview = wrapper.findAll('[role="tab"]').find((tab) => tab.text().includes('Overview'))
    await overview?.trigger('click')
    expect(wrapper.emitted('change')).toBeUndefined()
  })

  it('moves focus to the next tab with ArrowDown', async () => {
    const wrapper = mount(SubNav, {
      props: { groups, activeKey: 'overview' },
      attachTo: document.body,
    })
    const tabs = wrapper.findAll('[role="tab"]')
    ;(tabs[0].element as HTMLElement).focus()
    await tabs[0].trigger('keydown', { key: 'ArrowDown' })
    expect(document.activeElement).toBe(tabs[1].element)
    wrapper.unmount()
  })
})
