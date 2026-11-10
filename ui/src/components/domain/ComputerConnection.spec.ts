import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import ComputerConnection from './ComputerConnection.vue'
import { callOperation } from '@/lib/operations'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))

describe('ComputerConnection', () => {
  it('offers explicit native sign-in and existing folder setup without an executor', async () => {
    vi.mocked(callOperation).mockResolvedValue({ items: [] })
    const wrapper = mount(ComputerConnection, { props: { projectId: 12 } })
    await flushPromises()
    expect(wrapper.text()).toContain('No authorized computer')
    await wrapper.findAll('button').find(button => button.text() === 'Connect computer')!.trigger('click')
    expect(wrapper.get('pre').text()).toContain('stackos login')
    expect(wrapper.get('pre').text()).toContain('--project-id 12 --folder /path/to/existing/project')
  })

  it('keeps authorization distinct from execution readiness and shows unavailable state', async () => {
    vi.mocked(callOperation).mockResolvedValueOnce({ items: [{ connection_id: 'connection', status: 'authorized' }] })
    const wrapper = mount(ComputerConnection, { props: { projectId: 12 } })
    await flushPromises()
    expect(wrapper.text()).toContain('Local runtime and agent readiness are checked before work starts')
    vi.mocked(callOperation).mockRejectedValueOnce(new Error('Selected server unavailable'))
    await wrapper.findAll('button').find(button => button.text() === 'Refresh')!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Selected server unavailable')
    expect(wrapper.text()).not.toContain('Computer authorized.')
  })

  it('discards a late response after the project-keyed route remounts', async () => {
    let resolve!: (value: unknown) => void
    vi.mocked(callOperation).mockReturnValueOnce(new Promise(done => { resolve = done }))
    const previous = mount(ComputerConnection, { props: { projectId: 12 } })
    previous.unmount()
    vi.mocked(callOperation).mockResolvedValueOnce({ items: [] })
    const wrapper = mount(ComputerConnection, { props: { projectId: 13 } })
    await flushPromises()
    resolve({ items: [{ connection_id: 'old', status: 'authorized' }] })
    await flushPromises()
    expect(wrapper.text()).toContain('No authorized computer')
  })
})
