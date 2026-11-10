import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import WorkflowDetailView from './WorkflowDetailView.vue'
import { openView, plugin, workflow } from './plugin-domain/testSupport'

const client = vi.hoisted(() => ({ apiFetch: vi.fn() }))
vi.mock('@/lib/client', () => ({ apiFetch: client.apiFetch, formatApiError: (e: unknown) => String(e) }))
const wrappers: Awaited<ReturnType<typeof openView>>['wrapper'][] = []
async function open() {
  const result = await openView(WorkflowDetailView, '/projects/1/plugins/engineering/workflows/engineering.tracked-delivery')
  wrappers.push(result.wrapper); await flushPromises(); return result
}
describe('Workflow detail', () => {
  beforeEach(() => { client.apiFetch.mockReset().mockImplementation(async (url: string) => url.startsWith('/api/v1/plugins?') ? [plugin(), plugin('support')] : workflow()) })
  afterEach(() => { wrappers.forEach(wrapper => wrapper.unmount()); wrappers.length = 0; document.body.innerHTML = '' })
  it('reads the effective contract with dependency/checkpoint meaning and performs only reads', async () => {
    const { wrapper } = await open()
    expect(wrapper.get('h1').text()).toBe('Tracked delivery')
    expect(wrapper.get('ol').text()).toContain('After: Prepare')
    expect(wrapper.get('[aria-labelledby="workflow-checkpoints"]').text()).toContain('Stages: Check')
    expect(wrapper.get('[aria-labelledby="workflow-inputs"]').text()).toContain('Optional')
    expect(wrapper.text()).not.toMatch(/Ready for this project|Start run/)
    const disclosure = wrapper.get('details').element as HTMLDetailsElement
    disclosure.open = true
    await wrapper.findAll('button').find(button => button.text() === 'Refresh')!.trigger('click'); await flushPromises()
    expect((wrapper.get('details').element as HTMLDetailsElement).open).toBe(true)
    expect(client.apiFetch.mock.calls.every(([, options]) => !options?.method || options.method === 'GET')).toBe(true)
  })
  it('clears old detail on navigation, ignores late results and shows a different workflow without invented sections', async () => {
    let resolve!: (value: unknown) => void
    client.apiFetch.mockImplementation((url: string) => url.startsWith('/api/v1/plugins?') ? Promise.resolve([plugin(), plugin('support')])
      : url.includes('engineering.tracked-delivery') ? new Promise(value => { resolve = value }) : Promise.resolve({ ...workflow('support.investigate', 'support'), spec: { key: 'support.investigate', name: 'Investigate', steps: [{ id: 'read', title: 'Read issue' }] } }))
    const { wrapper, router } = await open()
    expect(wrapper.text()).toContain('Loading workflow')
    await router.push('/projects/1/plugins/support/workflows/support.investigate'); await flushPromises()
    resolve(workflow()); await flushPromises()
    expect(wrapper.get('h1').text()).toBe('Investigate')
    expect(wrapper.get('ol').text()).toContain('Read issue')
    expect(wrapper.find('[aria-labelledby="workflow-checkpoints"]').exists()).toBe(false)
    expect(wrapper.find('[aria-labelledby="workflow-outputs"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('No inputs declared.')
  })
  it('reports missing or mismatched sources without showing the previous workflow', async () => {
    const { wrapper } = await open()
    client.apiFetch.mockImplementation(async (url: string) => url.startsWith('/api/v1/plugins?') ? [plugin()] : workflow('wrong.key'))
    await wrapper.findAll('button').find(button => button.text() === 'Refresh')!.trigger('click'); await flushPromises()
    expect(wrapper.text()).toContain('does not match this page')
    expect(wrapper.find('ol').exists()).toBe(false)
    client.apiFetch.mockImplementation(async (url: string) => { if (url.startsWith('/api/v1/plugins?')) return [plugin()]; throw new Error('Source unavailable') })
    await wrapper.findAll('button').find(button => button.text() === 'Retry')!.trigger('click'); await flushPromises()
    expect(wrapper.text()).toContain('Source unavailable')
  })
})
