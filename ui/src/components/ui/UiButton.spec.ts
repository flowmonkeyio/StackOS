import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import UiButton from './UiButton.vue'

describe('UiButton', () => {
  it('renders supported button icons as inline SVGs', () => {
    const wrapper = mount(UiButton, {
      props: { iconLeft: 'plug-zap', iconRight: 'ban' },
      slots: { default: 'Test' },
    })

    expect(wrapper.findAll('svg.ui-button__icon')).toHaveLength(2)
    expect(wrapper.text()).toContain('Test')
  })

  it('renders the file-text details icon as inline SVG', () => {
    const wrapper = mount(UiButton, {
      props: { iconLeft: 'file-text' },
      slots: { default: 'Details' },
    })

    expect(wrapper.find('svg.ui-button__icon').exists()).toBe(true)
  })

  it.each(['settings', 'trash'])('renders the %s icon used by product buttons', (icon) => {
    const wrapper = mount(UiButton, {
      props: { iconLeft: icon },
      slots: { default: 'Action' },
    })

    expect(wrapper.find('svg.ui-button__icon').exists()).toBe(true)
  })

  it('does not reserve a fallback icon node for unknown icon names', () => {
    const wrapper = mount(UiButton, {
      props: { iconLeft: 'missing-icon' },
      slots: { default: 'Test' },
    })

    expect(wrapper.find('svg.ui-button__icon').exists()).toBe(false)
  })

  it('forwards form ownership for side-panel submit actions', () => {
    const wrapper = mount(UiButton, {
      props: { type: 'submit', form: 'connection-form' },
      slots: { default: 'Save' },
    })

    expect(wrapper.get('button').attributes('type')).toBe('submit')
    expect(wrapper.get('button').attributes('form')).toBe('connection-form')
  })
})
