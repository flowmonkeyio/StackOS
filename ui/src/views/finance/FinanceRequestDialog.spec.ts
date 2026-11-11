import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { callOperation } from '@/lib/operations'
import FinanceRequestDialog from './FinanceRequestDialog.vue'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))
afterEach(() => { vi.resetAllMocks(); document.body.innerHTML = '' })
const props = { projectId: 1, workspaceRef: 'finance:project:1', requestId: 7, requests: [] }
const request = { id: 7, project_id: 1, title: 'Original request', status: 'failed', metadata_json: { metadata_json: { workspace_ref: props.workspaceRef } } }
const setup = () => mount(FinanceRequestDialog, { props, attachTo: document.body, global: { stubs: { teleport: true } } })

describe('Finance contextual request reader', () => {
  it('shows unavailable and retries only the same safe read', async () => {
    vi.mocked(callOperation).mockRejectedValueOnce(new Error('Unavailable')).mockResolvedValueOnce(request)
    const wrapper = setup()
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('unavailable')
    await wrapper.findAll('button').find(button => button.text() === 'Retry request read')!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Original request')
    expect(vi.mocked(callOperation).mock.calls.map(([name]) => name)).toEqual(['agentRequest.get', 'agentRequest.get'])
    wrapper.unmount()
  })
  it.each([
    { ...request, id: 8 },
    { ...request, project_id: 2 },
    { ...request, metadata_json: { metadata_json: { workspace_ref: 'other' } } },
  ])('rejects mismatched request identity or Finance scope', async value => {
    vi.mocked(callOperation).mockResolvedValue(value)
    const wrapper = setup()
    await flushPromises()
    expect(wrapper.text()).toContain('unavailable')
    expect(wrapper.find('[data-test="finance-request-title"]').exists()).toBe(false)
    wrapper.unmount()
  })
  it('discards a late read after the Finance origin unmounts the old project reader', async () => {
    let resolveOld!: (value: unknown) => void
    vi.mocked(callOperation).mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve })).mockResolvedValueOnce({ ...request, project_id: 2, title: 'Second project', metadata_json: { metadata_json: { workspace_ref: 'finance:project:2' } } })
    const wrapper = setup()
    wrapper.unmount()
    const next = mount(FinanceRequestDialog, { props: { ...props, projectId: 2, workspaceRef: 'finance:project:2' }, global: { stubs: { teleport: true } } })
    await flushPromises()
    resolveOld(request)
    await flushPromises()
    expect(next.get('[data-test="finance-request-title"]').text()).toBe('Second project')
    expect(document.body.textContent).not.toContain('Original request')
    next.unmount()
  })
})
