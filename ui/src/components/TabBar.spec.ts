import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import TabBar from './TabBar.vue'

const tabs = [
  { key: 'overview', label: 'Overview' },
  { key: 'context', label: 'Context' },
  { key: 'runs', label: 'Runs' },
]

describe('TabBar', () => {
  it('renders one button per tab and marks the active one with aria-selected', () => {
    const w = mount(TabBar, { props: { tabs, activeKey: 'context' } })
    const buttons = w.findAll('button[role="tab"]')
    expect(buttons.length).toBe(3)
    expect(buttons.map((b) => b.attributes('aria-selected'))).toEqual([
      'false',
      'true',
      'false',
    ])
  })

  it('emits change with the clicked key when an inactive tab is clicked', async () => {
    const w = mount(TabBar, { props: { tabs, activeKey: 'context' } })
    const buttons = w.findAll('button[role="tab"]')
    await buttons[2].trigger('click')
    expect(w.emitted('change')).toBeTruthy()
    expect(w.emitted('change')![0][0]).toBe('runs')
  })

  it('does not emit change when the already-active tab is clicked', async () => {
    const w = mount(TabBar, { props: { tabs, activeKey: 'context' } })
    const buttons = w.findAll('button[role="tab"]')
    await buttons[1].trigger('click')
    expect(w.emitted('change')).toBeFalsy()
  })

  it('cycles focus on ArrowRight through the buttons', async () => {
    const w = mount(TabBar, {
      props: { tabs, activeKey: 'overview' },
      attachTo: document.body,
    })
    const buttons = w.findAll('button[role="tab"]')
    ;(buttons[0].element as HTMLElement).focus()
    await buttons[0].trigger('keydown', { key: 'ArrowRight' })
    expect(document.activeElement).toBe(buttons[1].element)
    await buttons[1].trigger('keydown', { key: 'ArrowRight' })
    expect(document.activeElement).toBe(buttons[2].element)
    await buttons[2].trigger('keydown', { key: 'ArrowRight' })
    // Wraps around.
    expect(document.activeElement).toBe(buttons[0].element)
    w.unmount()
  })

  it('skips disabled tabs during arrow-key navigation', async () => {
    const tabsWithDisabled = [
      { key: 'a', label: 'A' },
      { key: 'b', label: 'B', disabled: true },
      { key: 'c', label: 'C' },
    ]
    const w = mount(TabBar, {
      props: { tabs: tabsWithDisabled, activeKey: 'a' },
      attachTo: document.body,
    })
    const buttons = w.findAll('button[role="tab"]')
    ;(buttons[0].element as HTMLElement).focus()
    await buttons[0].trigger('keydown', { key: 'ArrowRight' })
    expect(document.activeElement).toBe(buttons[2].element)
    w.unmount()
  })

  it('uses one stage dropdown and only shows tabs from the active group', async () => {
    const groupedTabs = [
      { key: 'brief', label: 'Brief', group: 'Plan' },
      { key: 'sources', label: 'Sources', group: 'Plan' },
      { key: 'draft', label: 'Draft', group: 'Write' },
      { key: 'edited', label: 'Edited', group: 'Write' },
      { key: 'schema', label: 'Schema', group: 'Quality' },
    ]
    const w = mount(TabBar, {
      props: { tabs: groupedTabs, activeKey: 'draft', ariaLabel: 'Workflow tabs' },
      attachTo: document.body,
    })

    expect(w.get('[role="combobox"]').text()).toContain('Write')
    expect(w.findAll('button[role="tab"]').map((b) => b.text())).toEqual(['Draft', 'Edited'])

    await w.get('[role="combobox"]').trigger('click')
    await w.findAll('[role="option"]')[0].trigger('click')

    expect(w.emitted('change')?.[0][0]).toBe('brief')
    w.unmount()
  })
})
