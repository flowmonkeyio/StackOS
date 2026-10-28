import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { h } from 'vue'

import UiDropdownMenu from './UiDropdownMenu.vue'

enableAutoUnmount(afterEach)

describe('UiDropdownMenu', () => {
  it('returns focus to its trigger when Escape dismisses the menu', async () => {
    const wrapper = mount(UiDropdownMenu, {
      attachTo: document.body,
      props: { items: [{ key: 'edit', label: 'Edit' }] },
      slots: {
        trigger: ({ toggle }: { toggle: () => void }) =>
          h('button', { 'data-dropdown-trigger': '', type: 'button', onClick: toggle }, 'Open'),
      },
    })
    const trigger = wrapper.get('[data-dropdown-trigger]')
    ;(trigger.element as HTMLButtonElement).focus()
    await trigger.trigger('click')
    expect(document.activeElement).toBe(wrapper.get('[role="menu"]').element)

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()

    expect(wrapper.find('[role="menu"]').exists()).toBe(false)
    expect(document.activeElement).toBe(trigger.element)
    expect(wrapper.emitted('select')).toBeUndefined()
  })

  it('does not return trigger focus when an outside control dismisses the menu', async () => {
    const wrapper = mount(UiDropdownMenu, {
      attachTo: document.body,
      props: { items: [{ key: 'edit', label: 'Edit' }] },
      slots: {
        trigger: ({ toggle }: { toggle: () => void }) =>
          h('button', { 'data-dropdown-trigger': '', type: 'button', onClick: toggle }, 'Open'),
      },
    })
    await wrapper.get('[data-dropdown-trigger]').trigger('click')
    const outside = document.createElement('button')
    document.body.append(outside)
    outside.focus()
    outside.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    await flushPromises()

    expect(wrapper.find('[role="menu"]').exists()).toBe(false)
    expect(document.activeElement).toBe(outside)
    outside.remove()
  })

  it('renders supported item icons as inline SVGs', async () => {
    const wrapper = mount(UiDropdownMenu, {
      props: {
        items: [{ key: 'edit', label: 'Edit', icon: 'settings' }],
      },
      slots: {
        trigger: ({ toggle }: { toggle: () => void }) =>
          h('button', { 'data-dropdown-trigger': '', type: 'button', onClick: toggle }, 'Open'),
      },
    })

    await wrapper.find('[data-dropdown-trigger]').trigger('click')

    expect(wrapper.find('svg.ui-dropdown__icon').exists()).toBe(true)
  })
})
