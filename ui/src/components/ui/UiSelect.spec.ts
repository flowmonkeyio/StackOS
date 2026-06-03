import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import UiFormField from './UiFormField.vue'
import UiSelect from './UiSelect.vue'

describe('UiSelect', () => {
  it('renders the selected label and emits update/change when an option is selected', async () => {
    const wrapper = mount(UiSelect, {
      props: {
        modelValue: 'plan',
        options: [
          { value: 'plan', label: 'Plan' },
          { value: 'write', label: 'Write' },
        ],
      },
      attachTo: document.body,
    })

    expect(wrapper.get('[role="combobox"]').text()).toContain('Plan')

    await wrapper.get('[role="combobox"]').trigger('click')
    await wrapper.findAll('[role="option"]')[1].trigger('click')

    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual(['write'])
    expect(wrapper.emitted('change')?.[0]).toEqual(['write'])
    wrapper.unmount()
  })

  it('renders right-aligned option metadata in the button and menu', async () => {
    const wrapper = mount(UiSelect, {
      props: {
        modelValue: 'reporting',
        options: [
          {
            value: 'reporting',
            label: 'Reporting module',
            rightLabel: 'complete',
            rightMeta: '2/2 tasks',
            rightTone: 'success',
          },
          {
            value: 'setup',
            label: 'Setup',
            rightLabel: 'not started',
            rightMeta: '0/3 tasks',
            rightTone: 'neutral',
          },
        ],
      },
      attachTo: document.body,
    })

    const button = wrapper.get('[role="combobox"]')
    expect(button.text()).toContain('Reporting module')
    expect(button.text()).toContain('complete')
    expect(button.text()).toContain('2/2 tasks')

    await button.trigger('click')
    const option = wrapper.findAll('[role="option"]')[1]
    expect(option.text()).toContain('Setup')
    expect(option.text()).toContain('not started')
    expect(option.text()).toContain('0/3 tasks')
    wrapper.unmount()
  })

  it('supports keyboard open, movement, selection, and Escape close', async () => {
    const wrapper = mount(UiSelect, {
      props: {
        modelValue: 'a',
        options: [
          { value: 'a', label: 'A' },
          { value: 'b', label: 'B' },
          { value: 'c', label: 'C' },
        ],
      },
      attachTo: document.body,
    })
    const button = wrapper.get('[role="combobox"]')

    await button.trigger('keydown', { key: 'ArrowDown' })
    expect(button.attributes('aria-expanded')).toBe('true')
    await button.trigger('keydown', { key: 'ArrowDown' })
    await button.trigger('keydown', { key: 'Enter' })

    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual(['b'])

    await button.trigger('click')
    expect(button.attributes('aria-expanded')).toBe('true')
    await button.trigger('keydown', { key: 'Escape' })
    expect(button.attributes('aria-expanded')).toBe('false')
    wrapper.unmount()
  })

  it('filters searchable option lists by label, value, and group', async () => {
    const wrapper = mount(UiSelect, {
      props: {
        modelValue: 'meta-ads',
        searchable: true,
        searchPlaceholder: 'Search services',
        emptyLabel: 'No services found',
        options: [
          { value: 'meta-ads', label: 'Meta Ads', group: 'Media Buying' },
          { value: 'trackbooth', label: 'Trackbooth', group: 'Affiliation' },
        ],
      },
      attachTo: document.body,
    })
    const button = wrapper.get('[role="combobox"]')

    await button.trigger('click')
    const searchInput = wrapper.get<HTMLInputElement>('input[aria-label="Search options"]')
    expect(searchInput.attributes('placeholder')).toBe('Search services')

    await searchInput.setValue('affiliation')
    expect(wrapper.findAll('[role="option"]').map((option) => option.text())).toEqual([
      'Trackbooth',
    ])
    expect(wrapper.text()).toContain('Affiliation')

    await searchInput.trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual(['trackbooth'])

    await button.trigger('click')
    await wrapper.get<HTMLInputElement>('input[aria-label="Search options"]').setValue('missing')
    expect(wrapper.findAll('[role="option"]')).toHaveLength(0)
    expect(wrapper.text()).toContain('No services found')
    wrapper.unmount()
  })

  it('uses the surrounding UiFormField id for label association', () => {
    const wrapper = mount({
      components: { UiFormField, UiSelect },
      template: `
        <UiFormField label="Stage">
          <UiSelect model-value="plan" :options="[{ value: 'plan', label: 'Plan' }]" />
        </UiFormField>
      `,
    })

    const labelFor = wrapper.get('label').attributes('for')
    expect(labelFor).toBeTruthy()
    expect(wrapper.get('[role="combobox"]').attributes('id')).toBe(labelFor)
  })
})
