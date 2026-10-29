import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import UiButton from './UiButton.vue'
import UiIcon from './UiIcon.vue'
import { h } from 'vue'

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

  it('preserves the normal icon and label nodes while loading and blocks duplicate clicks', async () => {
    const wrapper = mount(UiButton, {
      props: { iconLeft: 'archive', iconRight: 'arrow-right' },
      slots: { default: 'Back up now' },
    })
    const button = wrapper.get('button').element
    const label = wrapper.get('.ui-button__label').element
    const icons = wrapper.findAll('svg.ui-button__icon').map((icon) => icon.element)
    await wrapper.setProps({ loading: true })
    expect(wrapper.get('button').element).toBe(button)
    expect(wrapper.get('.ui-button__label').element).toBe(label)
    for (const icon of icons) expect(button.contains(icon)).toBe(true)
    expect(wrapper.get('button').attributes('aria-busy')).toBe('true')
    expect(wrapper.get('button').attributes('disabled')).toBeDefined()
    expect(wrapper.get('.ui-button__spinner').attributes('aria-hidden')).toBe('true')
    expect(wrapper.text()).toBe('Back up now')
    await wrapper.trigger('click')
    expect(wrapper.emitted('click')).toBeUndefined()
    await wrapper.setProps({ loading: false })
    expect(wrapper.get('button').attributes('aria-busy')).toBeUndefined()
    expect(wrapper.get('button').attributes('disabled')).toBeUndefined()
    expect(wrapper.get('.ui-button__label').element).toBe(label)
    await wrapper.trigger('click')
    expect(wrapper.emitted('click')).toHaveLength(1)
  })

  it('preserves icon-only content and its accessible name across loading', async () => {
    const wrapper = mount(UiButton, {
      props: { iconOnly: true, ariaLabel: 'Refresh backups' },
      slots: { default: () => h(UiIcon, { name: 'refresh' }) },
    })
    const icon = wrapper.get('span.ui-button__icon').element
    expect(wrapper.get('span.ui-button__icon').classes()).toEqual(expect.arrayContaining(['inline-flex', 'items-center', 'justify-center']))
    await wrapper.setProps({ loading: true })
    expect(wrapper.get('span.ui-button__icon').element).toBe(icon)
    expect(wrapper.get('button').attributes('aria-label')).toBe('Refresh backups')
    expect(wrapper.get('button').attributes('aria-busy')).toBe('true')
    await wrapper.setProps({ loading: false })
    expect(wrapper.get('span.ui-button__icon').element).toBe(icon)
    expect(wrapper.get('button').attributes('aria-label')).toBe('Refresh backups')
  })
})
