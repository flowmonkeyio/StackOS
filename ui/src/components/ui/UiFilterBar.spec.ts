import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import UiFilterBar from './UiFilterBar.vue'

describe('applied filter chips', () => {
  it('keeps the remove icon outside the truncating label and names the native remove button', async () => {
    const value = 'A very long plugin name '.repeat(20)
    const wrapper = mount(UiFilterBar, {
      props: { active: [{ key: 'plugin', label: 'Plugin', value }] },
    })
    const chip = wrapper.get('button.ui-badge')
    const label = chip.get('.ui-badge__label')
    expect(chip.attributes('type')).toBe('button')
    expect(chip.attributes('aria-label')).toBe(`Remove Plugin filter: ${value}`)
    expect(chip.attributes('title')).toBe(`Plugin: ${value}`)
    expect(chip.classes()).toContain('focus-ring')
    expect(chip.classes()).toContain('max-w-full')
    expect(label.classes()).toContain('truncate')
    expect(label.text()).toContain(value.trim())
    expect(label.find('svg').exists()).toBe(false)
    const icon = chip.get('svg')
    expect(icon.element.parentElement).toBe(chip.element)
    expect(icon.classes()).toContain('shrink-0')
    expect(icon.attributes('aria-hidden')).toBe('true')
    await icon.trigger('click')
    expect(wrapper.emitted('remove')).toEqual([['plugin']])
    await wrapper.get('button.ui-button').trigger('click')
    expect(wrapper.emitted('clearAll')).toEqual([[]])
    await wrapper.setProps({ active: [] })
    expect(wrapper.find('.ui-badge').exists()).toBe(false)
    wrapper.unmount()
  })

  it('retains search and control slots for consumers without applied chips', async () => {
    const wrapper = mount(UiFilterBar, {
      props: { search: 'service' },
      slots: { default: '<button>Filter</button>', right: '<button>Sort</button>' },
    })
    expect(wrapper.text()).toContain('Filter')
    expect(wrapper.text()).toContain('Sort')
    expect(wrapper.find('.ui-badge').exists()).toBe(false)
    await wrapper.get('input').setValue('account')
    expect(wrapper.emitted('update:search')).toEqual([['account']])
    await wrapper.setProps({ noSearch: true })
    expect(wrapper.find('input').exists()).toBe(false)
    wrapper.unmount()
  })
})
