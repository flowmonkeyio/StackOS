import { describe, expect, it } from 'vitest'
import { DOMWrapper, flushPromises, mount } from '@vue/test-utils'

import UiFormField from './UiFormField.vue'
import UiSelect from './UiSelect.vue'
import UiDialog from './UiDialog.vue'

describe('UiSelect', () => {
  it('portals its menu and gives Escape and logical Tab ownership back to its dialog', async () => {
    const wrapper = mount({
      components: { UiDialog, UiSelect },
      data: () => ({ shown: true }),
      template: `<UiDialog v-model="shown" title="Settings"><UiSelect searchable :options="['A', 'B']" /><button id="after-select">After</button></UiDialog>`,
    }, { attachTo: document.body })
    await flushPromises()
    const trigger = document.querySelector<HTMLButtonElement>('.ui-select__button')!
    trigger.focus()
    trigger.click()
    await flushPromises()
    expect(document.querySelector('[role="dialog"]')!.contains(document.querySelector('[role="listbox"]'))).toBe(false)
    expect(document.activeElement?.getAttribute('aria-label')).toBe('Search options')
    document.activeElement!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
    await flushPromises()
    expect(document.querySelector('[role="listbox"]')).toBeNull()
    expect(document.querySelector('[role="dialog"]')).not.toBeNull()
    expect(document.activeElement).toBe(trigger)
    for (const shiftKey of [false, true]) {
      trigger.click()
      await flushPromises()
      const event = new KeyboardEvent('keydown', { key: 'Tab', shiftKey, bubbles: true, cancelable: true })
      document.activeElement!.dispatchEvent(event)
      await flushPromises()
      expect(document.querySelector('[role="listbox"]')).toBeNull()
      expect(document.activeElement).toBe(trigger)
      expect(event.defaultPrevented).toBe(false)
    }
    trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    expect(document.querySelector('[role="dialog"]')).toBeNull()
    wrapper.unmount()
  })

  it('renders the selected label and emits update/change when an option is selected', async () => {
    const wrapper = mount(UiSelect, {
      props: {
        modelValue: 'plan',
        options: [
          { value: 'plan', label: 'Plan' },
          { value: 'write', label: 'Write' },
        ],
      },
      global: { stubs: { teleport: true } },
      attachTo: document.body,
    })

    expect(wrapper.get('[role="combobox"]').text()).toContain('Plan')

    await wrapper.get('[role="combobox"]').trigger('click')
    await wrapper.findAll('[role="option"]')[1].trigger('click')

    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual(['write'])
    expect(wrapper.emitted('change')?.[0]).toEqual(['write'])
    wrapper.unmount()
  })

  it('retires an open menu when disabled and preserves the underlying scroll lock', async () => {
    const wrapper = mount({
      components: { UiDialog, UiSelect },
      data: () => ({ pending: false }),
      template: `<UiDialog :model-value="true" title="Pending" no-escape static-backdrop><UiSelect :disabled="pending" :options="['A', 'B']" /></UiDialog>`,
    }, { attachTo: document.body })
    await flushPromises()
    document.querySelector<HTMLButtonElement>('.ui-select__button')!.click()
    await flushPromises()
    await wrapper.setData({ pending: true })
    await flushPromises()
    expect(document.querySelector('[role="listbox"]')).toBeNull()
    expect(document.body.style.overflow).toBe('hidden')
    document.activeElement!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    expect(document.querySelector('[role="dialog"]')).not.toBeNull()
    wrapper.unmount()
    expect(document.body.style.overflow).not.toBe('hidden')
  })

  it('closes on outside pointer without stealing its destination, and standalone menus do not lock the page', async () => {
    const wrapper = mount(UiSelect, { props: { options: ['A', 'B'] }, attachTo: document.body })
    await wrapper.get('button').trigger('click')
    expect(document.body.style.overflow).not.toBe('hidden')
    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }))
    await flushPromises()
    expect(document.querySelector('[role="listbox"]')).toBeNull()
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
      global: { stubs: { teleport: true } },
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
      global: { stubs: { teleport: true } },
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
      global: { stubs: { teleport: true } },
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
      global: { stubs: { teleport: true } },
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
    const menu = new DOMWrapper(document.body)
    const button = wrapper.get('[role="combobox"]')

    await button.trigger('click')
    const searchInput = menu.get<HTMLInputElement>('input[aria-label="Search options"]')
    expect(searchInput.attributes('placeholder')).toBe('Search services')
    expect(searchInput.attributes('role')).toBe('combobox')
    expect(searchInput.attributes('aria-controls')).toBeTruthy()
    expect(searchInput.attributes('aria-activedescendant')).toBeTruthy()
    expect(menu.findAll('[role="option"]').every((option) => option.attributes('tabindex') === '-1')).toBe(true)

    await searchInput.trigger('keydown', { key: 'ArrowDown' })
    expect(searchInput.attributes('aria-activedescendant')).toContain('option-1')

    await searchInput.trigger('keydown', { key: 'Tab' })
    expect(button.attributes('aria-expanded')).toBe('false')

    await button.trigger('click')
    const reopenedSearchInput = menu.get<HTMLInputElement>('input[aria-label="Search options"]')

    await reopenedSearchInput.setValue('affiliation')
    expect(menu.findAll('[role="option"]').map((option) => option.text())).toEqual([
      'Trackbooth',
    ])
    expect(menu.text()).toContain('Affiliation')

    await reopenedSearchInput.trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual(['trackbooth'])

    await button.trigger('click')
    await menu.get<HTMLInputElement>('input[aria-label="Search options"]').setValue('missing')
    expect(menu.findAll('[role="option"]')).toHaveLength(0)
    expect(menu.text()).toContain('No services found')
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
