import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import UiTooltip from './UiTooltip.vue'

afterEach(() => { vi.useRealTimers() })

describe('tooltip focus and controlled disclosure', () => {
  it('opens uncontrolled full text on keyboard focus and closes on blur', async () => {
    vi.useFakeTimers()
    const wrapper = mount(UiTooltip, { props: { content: 'Complete recorded task title' }, slots: { default: '<a href="/task">Short title</a>' } })
    try {
      expect(wrapper.find('[role="tooltip"]').exists()).toBe(false)
      await wrapper.get('a').trigger('focusin')
      await vi.advanceTimersByTimeAsync(350)
      expect(wrapper.get('[role="tooltip"]').text()).toBe('Complete recorded task title')
      await wrapper.get('a').trigger('focusout')
      expect(wrapper.find('[role="tooltip"]').exists()).toBe(false)
      await wrapper.setProps({ open: false })
      await wrapper.get('a').trigger('focusin')
      await vi.advanceTimersByTimeAsync(350)
      expect(wrapper.find('[role="tooltip"]').exists()).toBe(false)
      await wrapper.setProps({ open: true })
      expect(wrapper.find('[role="tooltip"]').exists()).toBe(true)
    } finally { wrapper.unmount() }
  })
})
