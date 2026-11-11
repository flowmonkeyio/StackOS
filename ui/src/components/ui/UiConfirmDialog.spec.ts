import { afterEach, describe, expect, it } from 'vitest'
import { DOMWrapper, mount, type VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'
import UiConfirmDialog from './UiConfirmDialog.vue'

let wrapper: VueWrapper | undefined
afterEach(() => { wrapper?.unmount(); wrapper = undefined })
function dialog() { return new DOMWrapper(document.querySelector<HTMLElement>('[role="dialog"]')!) }

describe('UiConfirmDialog', () => {
  it('places the accessible explanation and supplemental content between title and actions', async () => {
    wrapper = mount(UiConfirmDialog, {
      props: { modelValue: true, title: 'Remove automation?', description: 'Future runs stop; history is retained.', tone: 'danger', confirmLabel: 'Remove', cancelLabel: 'Keep' },
      slots: { default: '<p>Supplemental outcome</p>' },
    })
    await nextTick()
    const frame = dialog()
    expect(frame.get('.ui-dialog__header').text()).toBe('Remove automation?')
    expect(frame.get('.ui-dialog__body').text()).toBe('Future runs stop; history is retained.Supplemental outcome')
    const description = document.getElementById(frame.attributes('aria-describedby')!)!
    expect(frame.get('.ui-dialog__body').element.contains(description)).toBe(true)
    const actions = frame.findAll('.ui-dialog__footer button')
    expect(actions.map(button => button.text())).toEqual(['Keep', 'Remove'])
    expect(actions[1]!.classes()).toContain('bg-danger')
    await actions[1]!.trigger('click')
    expect(wrapper.emitted('confirm')).toHaveLength(1)
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    await actions[0]!.trigger('click')
    expect(wrapper.emitted('cancel')).toHaveLength(1)
    expect(wrapper.emitted('update:modelValue')).toEqual([[false]])
    await wrapper.setProps({ description: undefined })
    expect(dialog().attributes('aria-describedby')).toBeUndefined()
    expect(dialog().get('.ui-dialog__body').text()).toBe('Supplemental outcome')
  })

  it('keeps pending actions inert and retains Escape/backdrop close requests', async () => {
    wrapper = mount(UiConfirmDialog, { props: { modelValue: true, title: 'Confirm', description: 'Explanation', loading: true } })
    await nextTick()
    const actions = dialog().findAll('.ui-dialog__footer button')
    expect(actions.every(button => button.attributes('disabled') !== undefined)).toBe(true)
    expect(actions[1]!.attributes('aria-busy')).toBe('true')
    for (const action of actions) await action.trigger('click')
    expect(wrapper.emitted('confirm')).toBeUndefined()
    expect(wrapper.emitted('cancel')).toBeUndefined()
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await new DOMWrapper(document.querySelector('.ui-dialog__overlay')!).trigger('click')
    expect(wrapper.emitted('update:modelValue')).toEqual([[false], [false]])
  })
})
