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

  it('supports custom selected-value and option presentation', async () => {
    const wrapper = mount(UiSelect, {
      props: {
        modelValue: 'slack-bot',
        options: [{ value: 'slack-bot', label: 'Slack Bot' }],
      },
      slots: {
        selected: '<template #selected="{ option }"><strong class="selected-custom">{{ option.label }} selected</strong></template>',
        option: '<template #option="{ option }"><span class="option-custom">Icon · {{ option.label }}</span></template>',
      },
      attachTo: document.body,
    })

    expect(wrapper.get('.selected-custom').text()).toBe('Slack Bot selected')

    await wrapper.get('[role="combobox"]').trigger('click')
    expect(wrapper.get('.option-custom').text()).toBe('Icon · Slack Bot')
    wrapper.unmount()
  })

  it('clips long option content instead of allowing horizontal menu overflow', async () => {
    const wrapper = mount(UiSelect, {
      props: {
        modelValue: 'very-long',
        options: [
          {
            value: 'very-long',
            label: 'Very long service label that must remain inside the select dropdown width',
            group: 'Very long group name that must not stretch the dropdown panel',
            rightLabel: 'connected-with-a-very-long-state',
            rightMeta: 'very-long-provider-ref.local.example',
          },
        ],
      },
      attachTo: document.body,
    })

    await wrapper.get('[role="combobox"]').trigger('click')

    const listbox = wrapper.get('[role="listbox"]')
    expect(listbox.classes()).toContain('min-w-0')
    expect((listbox.element.parentElement as HTMLElement).className).toContain('overflow-x-hidden')

    const group = wrapper.get('[role="listbox"] > div')
    expect(group.classes()).toContain('truncate')

    const option = wrapper.get('[role="option"]')
    expect(option.classes()).toContain('overflow-hidden')
    expect(option.find('span').classes()).toContain('truncate')
    expect(option.get('.ui-select__right-meta').classes()).toContain('ui-select__right-meta')
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
    expect(searchInput.attributes('role')).toBe('combobox')
    expect(searchInput.attributes('aria-controls')).toBeTruthy()
    expect(searchInput.attributes('aria-activedescendant')).toBeTruthy()
    expect(wrapper.findAll('[role="option"]').every((option) => option.attributes('tabindex') === '-1')).toBe(true)

    await searchInput.trigger('keydown', { key: 'ArrowDown' })
    expect(searchInput.attributes('aria-activedescendant')).toContain('option-1')

    await searchInput.trigger('keydown', { key: 'Tab' })
    expect(button.attributes('aria-expanded')).toBe('false')

    await button.trigger('click')
    const reopenedSearchInput = wrapper.get<HTMLInputElement>('input[aria-label="Search options"]')

    await reopenedSearchInput.setValue('affiliation')
    expect(wrapper.findAll('[role="option"]').map((option) => option.text())).toEqual([
      'Trackbooth',
    ])
    expect(wrapper.text()).toContain('Affiliation')

    await reopenedSearchInput.trigger('keydown', { key: 'Enter' })
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
