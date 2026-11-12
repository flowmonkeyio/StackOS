import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import type { SchemaDeliveryCapacityOut } from '@/api'
import { callOperation } from '@/lib/operations'
import DeliveryCapacityControl from './DeliveryCapacityControl.vue'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))
const state = (limit = 3, running = 4): SchemaDeliveryCapacityOut => ({
  scope: 'local-user-daemon', configured_limit: limit, running_count: running,
  queued_count: 2, supported_min: 1, supported_max: 32,
})
let wrapper: ReturnType<typeof mount>
afterEach(() => { wrapper?.unmount(); document.body.innerHTML = ''; vi.resetAllMocks() })

async function setup() {
  vi.mocked(callOperation).mockResolvedValue(state())
  wrapper = mount(DeliveryCapacityControl, { attachTo: document.body })
  await flushPromises()
  return wrapper
}
const open = async () => { await wrapper.get('[data-popover-trigger]').trigger('click'); await flushPromises() }
const save = async () => { await wrapper.get('form').trigger('submit'); await flushPromises() }

describe('DeliveryCapacityControl', () => {
  it('reads daemon counts, steps a draft and uses the server save readback', async () => {
    await setup()
    expect(wrapper.get('[data-popover-trigger]').text()).toBe('4 of 3 running')
    await open()
    expect(wrapper.get('[aria-label="Decrease concurrent tasks"]').find('svg').exists()).toBe(true)
    expect(wrapper.get('[aria-label="Increase concurrent tasks"]').find('svg').exists()).toBe(true)
    expect(wrapper.text()).toContain('across all projects')
    expect(wrapper.text()).toContain('Running tasks continue')
    await wrapper.get('[aria-label="Increase concurrent tasks"]').trigger('click')
    expect((wrapper.get('input').element as HTMLInputElement).value).toBe('4')
    await wrapper.get('input').setValue(6)
    vi.mocked(callOperation).mockResolvedValueOnce({ data: state(5, 4) })
    await save()
    expect(callOperation).toHaveBeenLastCalledWith('tracker.setDeliveryCapacity', { concurrent_tasks: 6 })
    expect(wrapper.get('[data-popover-trigger]').text()).toBe('4 of 5 running')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(document.activeElement).toBe(wrapper.get('[data-popover-trigger]').element)
  })

  it('retains invalid and failed drafts without changing the effective limit', async () => {
    await setup(); await open()
    await wrapper.get('input').setValue(33)
    await save()
    expect(wrapper.get('[role="alert"]').text()).toContain('whole number from 1 to 32')
    expect(vi.mocked(callOperation).mock.calls.every(([name]) => name === 'tracker.deliveryCapacity')).toBe(true)
    await wrapper.get('input').setValue(2.5); await save()
    expect(wrapper.get('[role="alert"]').text()).toContain('whole number')
    await wrapper.get('input').setValue(''); await save()
    expect(wrapper.get('[role="alert"]').text()).toContain('whole number')
    await wrapper.get('input').setValue(2)
    vi.mocked(callOperation).mockRejectedValueOnce(new Error('Storage unavailable'))
    await save()
    expect(wrapper.get('[role="alert"]').text()).toContain('Storage unavailable')
    expect((wrapper.get('input').element as HTMLInputElement).value).toBe('2')
    expect(wrapper.get('[data-popover-trigger]').text()).toBe('4 of 3 running')
  })

  it('discards dismissed edits, restores focus and never writes on open or dismiss', async () => {
    await setup(); await open()
    await wrapper.get('input').setValue(9)
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    expect(document.activeElement).toBe(wrapper.get('[data-popover-trigger]').element)
    await open()
    expect((wrapper.get('input').element as HTMLInputElement).value).toBe('3')
    document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    await flushPromises()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(vi.mocked(callOperation).mock.calls.every(([name, args]) => name === 'tracker.deliveryCapacity' && !('project_id' in args))).toBe(true)
  })

  it('keeps the save single-flight and fences a read that began before it', async () => {
    await setup(); await open()
    let finishRead!: (value: SchemaDeliveryCapacityOut) => void
    vi.mocked(callOperation).mockImplementationOnce(() => new Promise(resolve => { finishRead = resolve }))
    const refresh = (wrapper.vm as unknown as { refresh: () => Promise<void> }).refresh()
    await wrapper.get('input').setValue(8)
    let finishSave!: (value: unknown) => void
    vi.mocked(callOperation).mockImplementationOnce(() => new Promise(resolve => { finishSave = resolve }))
    await wrapper.get('form').trigger('submit')
    await wrapper.get('form').trigger('submit')
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
    expect(vi.mocked(callOperation).mock.calls.filter(([name]) => name === 'tracker.setDeliveryCapacity')).toHaveLength(1)
    finishSave({ data: state(8) }); await flushPromises()
    finishRead(state(3)); await refresh; await flushPromises()
    expect(wrapper.get('[data-popover-trigger]').text()).toBe('4 of 8 running')
  })

  it('shows unavailable reads instead of inventing a capacity or zero count', async () => {
    vi.mocked(callOperation).mockRejectedValue(new Error('Service unavailable'))
    wrapper = mount(DeliveryCapacityControl)
    await flushPromises(); await open()
    expect(wrapper.get('[data-popover-trigger]').text()).toBe('Capacity unavailable')
    expect(wrapper.text()).toContain('Service unavailable')
    expect(wrapper.find('input').exists()).toBe(false)
  })
})
