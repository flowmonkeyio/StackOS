import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import TaskDeliveryStop from './TaskDeliveryStop.vue'

describe('TaskDeliveryStop public reason', () => {
  it.each([
    ['Artifact:file:///private/tmp/native.json', 'Artifact:[local path]'],
    ['Artifact:C:/Users/operator/native.json', 'Artifact:[local path]'],
    [String.raw`Artifact:C:\Users\operator\native.json`, 'Artifact:[local path]'],
    ['Native failed at "C:/Users/operator/Private Client/native.json".', 'Native failed at "[local path]".'],
    [String.raw`Native failed at "C:\Users\operator\Private Client\native.json".`, 'Native failed at "[local path]".'],
    ['Artifact:"file://localhost/private/tmp/Private Client/native.json"; See https://example.test/help?next=/docs', 'Artifact:"[local path]"; See https://example.test/help?next=/docs'],
  ])('masks reviewed boundary in detailed text and both tooltips: %s', async (reason, expected) => {
    const delivery = { status: 'stopped' as const, ticketKey: null, reason }
    const wrapper = mount(TaskDeliveryStop, { props: { delivery, detailed: true } })
    expect(wrapper.get('.task-delivery-stop__reason').text()).toBe(expected)
    expect(wrapper.get('.task-delivery-stop').attributes('title')).toBe(expected)
    await wrapper.setProps({ detailed: false })
    expect(wrapper.get('.task-delivery-stop').attributes('title')).toBe(expected)
    wrapper.unmount()
  })
  it('sanitizes direct reason props in both text and tooltip and clears on resume', async () => {
    const reason = String.raw`Native failed at "file:///private/tmp/native.json", C:/Users/operator/native.json and \\workstation\share\native.json. See https://example.test/help`
    const safe = 'Native failed at "[local path]", [local path] and [local path]. See https://example.test/help'
    const delivery = { status: 'stopped' as const, ticketKey: null, reason }
    const wrapper = mount(TaskDeliveryStop, { props: { delivery, detailed: true } })
    expect(wrapper.get('.task-delivery-stop__reason').text()).toBe(safe)
    expect(wrapper.get('.task-delivery-stop').attributes('title')).toBe(safe)
    await wrapper.setProps({ detailed: false })
    expect(wrapper.find('.task-delivery-stop__reason').exists()).toBe(false)
    expect(wrapper.get('.task-delivery-stop').attributes('title')).toBe(safe)
    await wrapper.setProps({ delivery: { ...delivery, status: 'running' } })
    expect(wrapper.find('.task-delivery-stop').exists()).toBe(false)
    wrapper.unmount()
  })
})
