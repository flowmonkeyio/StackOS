import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import UiPopover from './UiPopover.vue'

afterEach(() => { vi.restoreAllMocks() })
describe('anchored popovers', () => {
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
