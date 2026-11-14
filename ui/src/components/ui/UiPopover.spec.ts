import { DOMWrapper, enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import UiPopover from './UiPopover.vue'
import UiSelect from './UiSelect.vue'
import UiDialog from './UiDialog.vue'

enableAutoUnmount(afterEach)
afterEach(() => { vi.restoreAllMocks() })
describe('anchored popovers', () => {
  it('keeps modal scroll lock and Escape ownership through nested popover and select closure', async () => {
    const wrapper = mount({
      components: { UiDialog, UiPopover, UiSelect },
      data: () => ({ dialog: true, popover: false }),
      template: `<UiDialog v-model="dialog" title="Settings"><UiPopover v-model="popover"><template #trigger="{ toggle }"><button data-popover-trigger @click="toggle">Edit</button></template><UiSelect :options="['A', 'B']" /></UiPopover></UiDialog>`,
    }, { attachTo: document.body })
    await flushPromises()
    const body = new DOMWrapper(document.body)
    await body.get('[data-popover-trigger]').trigger('click')
    await flushPromises()
    await body.get('.ui-select__button').trigger('click')
    await flushPromises()
    for (const selector of ['.ui-select__menu', '.ui-popover__panel']) {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
      await flushPromises()
      expect(body.find(selector).exists()).toBe(false)
      expect(body.find('.ui-dialog').exists()).toBe(true)
      expect(document.body.style.overflow).toBe('hidden')
    }
    expect(document.activeElement).toBe(body.get('[data-popover-trigger]').element)
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    expect(body.find('.ui-dialog').exists()).toBe(false)
    expect(document.body.style.overflow).not.toBe('hidden')
    wrapper.unmount()
  })

  it('owns the pointer and keyboard interactions of a body-portaled searchable select', async () => {
    const wrapper = mount({
      components: { UiPopover, UiSelect },
      data: () => ({ open: false }),
      template: `<UiPopover v-model="open" aria-label="Filter"><template #trigger="{ toggle }"><button data-popover-trigger @click="toggle">Filter</button></template><UiSelect searchable :options="['Alpha', 'Beta']" /><button>Apply</button></UiPopover>`,
    }, { attachTo: document.body })
    await wrapper.get('[data-popover-trigger]').trigger('click')
    await flushPromises()
    const trigger = wrapper.get<HTMLButtonElement>('.ui-select__button')
    trigger.element.focus()
    await trigger.trigger('click')
    await flushPromises()
    const body = new DOMWrapper(document.body)
    const menu = body.get<HTMLElement>('.ui-select__menu')
    const panel = wrapper.get<HTMLElement>('.ui-popover__panel')
    expect(panel.element.contains(menu.element)).toBe(false)
    expect(Number(menu.element.style.zIndex)).toBeGreaterThan(Number(panel.element.style.zIndex))
    const search = body.get<HTMLInputElement>('input[aria-label="Search options"]')
    await search.trigger('pointerdown')
    await search.trigger('mousedown')
    await search.trigger('click')
    search.element.focus()
    await search.setValue('Beta')
    expect(wrapper.find('.ui-popover__panel').exists()).toBe(true)
    expect(body.findAll('[role="option"]').map(option => option.text())).toEqual(['Beta'])
    await search.trigger('keydown', { key: 'Escape' })
    expect(wrapper.find('.ui-popover__panel').exists()).toBe(true)
    expect(body.find('.ui-select__menu').exists()).toBe(false)
    expect(document.activeElement).toBe(trigger.element)
    await trigger.trigger('click')
    await flushPromises()
    await body.findAll('[role="option"]').find(option => option.text() === 'Beta')!.trigger('click')
    expect(wrapper.findComponent(UiSelect).emitted('update:modelValue')?.[0]).toEqual(['Beta'])
    expect(wrapper.find('.ui-popover__panel').exists()).toBe(true)
    expect(document.activeElement).toBe(trigger.element)
    await trigger.trigger('keydown', { key: 'Escape' })
    await flushPromises()
    expect(wrapper.find('.ui-popover__panel').exists()).toBe(false)
    expect(document.activeElement).toBe(wrapper.get('[data-popover-trigger]').element)
    await wrapper.get('[data-popover-trigger]').trigger('click')
    await flushPromises()
    document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    await flushPromises()
    expect(wrapper.find('.ui-popover__panel').exists()).toBe(false)
  })

  it('uses the external chip rectangle, flips above a bottom edge and clamps to the viewport', async () => {
    const chip = document.createElement('button')
    document.body.append(chip)
    vi.spyOn(chip, 'getBoundingClientRect').mockReturnValue({ left: 950, right: 1010, top: 700, bottom: 728, width: 60, height: 28 } as DOMRect)
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      return this.classList.contains('ui-popover__panel') ? { width: 280, height: 200 } as DOMRect : { width: 0, height: 0 } as DOMRect
    })
    const wrapper = mount(UiPopover, { attachTo: document.body, props: { modelValue: true, anchor: chip, width: 280 }, slots: { default: '<button>Editor</button>' } })
    await flushPromises()
    const panel = wrapper.get<HTMLElement>('[role="dialog"]').element
    expect(panel.style.left).toBe(`${window.innerWidth - 280 - 8}px`)
    expect(panel.style.top).toBe('496px')
    // Placement follows the actual anchor during scrolling and viewport changes.
    vi.mocked(chip.getBoundingClientRect).mockReturnValue({ left: 20, right: 80, top: 50, bottom: 78, width: 60, height: 28 } as DOMRect)
    document.dispatchEvent(new Event('scroll'))
    await flushPromises()
    expect(panel.style.left).toBe('20px')
    expect(panel.style.top).toBe('82px')
    await wrapper.setProps({ modelValue: false })
    expect(document.activeElement).toBe(chip)
    wrapper.unmount(); chip.remove()
  })
  it('keeps anchor clicks inside, closes on Escape/outside and returns to a surviving overflow trigger', async () => {
    const chip = document.createElement('button')
    const overflow = document.createElement('button')
    document.body.append(chip, overflow)
    const wrapper = mount(UiPopover, { attachTo: document.body, props: { modelValue: true, anchor: chip, returnFocus: overflow }, slots: { default: '<button>Editor</button>' } })
    chip.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    chip.remove()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    expect(wrapper.emitted('update:modelValue')).toEqual([[false]])
    await wrapper.setProps({ modelValue: false })
    expect(document.activeElement).toBe(overflow)
    await wrapper.setProps({ modelValue: true })
    document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    expect(wrapper.emitted('update:modelValue')).toEqual([[false], [false]])
    wrapper.unmount(); overflow.remove()
  })
})
