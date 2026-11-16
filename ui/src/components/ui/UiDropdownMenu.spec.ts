import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { h } from 'vue'

import UiDropdownMenu from './UiDropdownMenu.vue'

enableAutoUnmount(afterEach)
afterEach(() => vi.restoreAllMocks())

describe('UiDropdownMenu', () => {
  it('portals outside a scrolling ancestor, clamps to the viewport and accepts panel clicks', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const select = vi.fn()
    const wrapper = mount(UiDropdownMenu, {
      attachTo: document.body,
      props: { portalTarget: host, items: [{ key: 'rename', label: 'Rename project…', onSelect: select }] },
      slots: { trigger: ({ toggle }: { toggle: () => void }) => h('button', { 'data-dropdown-trigger': '', onClick: toggle }, 'Open') },
    })
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      return this.matches('[role="menu"]')
        ? { left: 0, right: 180, top: 0, bottom: 200, width: 180, height: 200 } as DOMRect
        : { left: 1000, right: 1020, top: 740, bottom: 760, width: 20, height: 20 } as DOMRect
    })
    await wrapper.get('button').trigger('click')
    await flushPromises()
    const panel = host.querySelector<HTMLElement>('[role="menu"]')!
    expect(panel).not.toBeNull()
    expect(panel.style.position).toBe('fixed')
    expect(parseFloat(panel.style.left)).toBe(window.innerWidth - 180 - 12)
    expect(parseFloat(panel.style.top)).toBe(536)
    window.dispatchEvent(new Event('resize'))
    await flushPromises()
    expect(parseFloat(panel.style.left) + 180).toBeLessThanOrEqual(window.innerWidth - 12)
    panel.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    await flushPromises()
    expect(host.querySelector('[role="menu"]')).not.toBeNull()
    panel.querySelector('button')!.click()
    await flushPromises()
    expect(select).toHaveBeenCalledTimes(1)
    expect(host.querySelector('[role="menu"]')).toBeNull()
    wrapper.unmount()
    host.remove()
  })
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
