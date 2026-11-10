import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import WorkflowDetailView from './WorkflowDetailView.vue'
import { openView, plugin, workflow } from './plugin-domain/testSupport'

const client = vi.hoisted(() => ({ apiFetch: vi.fn() }))
vi.mock('@/lib/client', () => ({ apiFetch: client.apiFetch, formatApiError: (e: unknown) => String(e) }))
const wrappers: Awaited<ReturnType<typeof openView>>['wrapper'][] = []
async function open(path = '/projects/1/plugins/engineering/workflows/engineering.tracked-delivery') {
  const result = await openView(WorkflowDetailView, path)
  wrappers.push(result.wrapper); await flushPromises(); return result
}
describe('Workflow detail', () => {
  beforeEach(() => { client.apiFetch.mockReset().mockImplementation(async (url: string) => url.startsWith('/api/v1/plugins?') ? [plugin(), plugin('support')] : workflow()) })
  afterEach(() => { wrappers.forEach(wrapper => wrapper.unmount()); wrappers.length = 0; document.body.innerHTML = '' })
  function configuredWorkflow(key = 'customer.weekly-review') {
    const result = workflow(key)
    result.summary.plugin_slug = null
    result.summary.source = 'project'
    result.spec.name = 'Customer weekly review'
    return result
  }
  it('inspects a non-plugin workflow at its project origin without requiring a plugin catalog read', async () => {
    client.apiFetch.mockResolvedValue(configuredWorkflow())
    const { wrapper } = await open('/projects/1/workflows/customer.weekly-review')
    expect(wrapper.get('h1').text()).toBe('Customer weekly review')
    expect(wrapper.get('a').attributes('href')).toBe('/projects/1')
    expect(wrapper.get('a').text()).toBe('Home / Workflows')
    expect(wrapper.get('ol').text()).toContain('Prepare')
    expect(client.apiFetch.mock.calls.map(([url]) => url)).toEqual(['/api/v1/projects/1/workflow-templates/customer.weekly-review'])
  })
  it('retains project/key identity through route reuse and rejects stale non-plugin detail', async () => {
    let resolveOld!: (value: unknown) => void
    client.apiFetch.mockReturnValue(new Promise(resolve => { resolveOld = resolve }))
    const { wrapper, router } = await open('/projects/1/workflows/customer.weekly-review')
    client.apiFetch.mockImplementation(async (url: string) => url.startsWith('/api/v1/plugins?') ? [plugin()] : workflow())
    await router.push('/projects/1/plugins/engineering/workflows/engineering.tracked-delivery'); await flushPromises()
    resolveOld(configuredWorkflow()); await flushPromises()
    expect(wrapper.get('h1').text()).toBe('Tracked delivery')
    client.apiFetch.mockResolvedValue(configuredWorkflow('user.weekly-review'))
    await router.push('/projects/2/workflows/user.weekly-review'); await flushPromises()
    expect(client.apiFetch).toHaveBeenLastCalledWith('/api/v1/projects/2/workflow-templates/user.weekly-review')
    expect(wrapper.get('a').attributes('href')).toBe('/projects/2')
    expect(wrapper.get('h1').text()).toBe('Customer weekly review')
  })
  it('reports unavailable and mismatched non-plugin sources, then retries the same exact workflow', async () => {
    client.apiFetch.mockRejectedValue(new Error('Workflow not found'))
    const { wrapper } = await open('/projects/1/workflows/customer.weekly-review')
    expect(wrapper.text()).toContain('Workflow not found')
    expect(wrapper.find('ol').exists()).toBe(false)
    client.apiFetch.mockResolvedValue(workflow('customer.weekly-review'))
    await wrapper.findAll('button').find(button => button.text() === 'Retry')!.trigger('click'); await flushPromises()
    expect(wrapper.text()).toContain('does not match this page')
    client.apiFetch.mockResolvedValue(configuredWorkflow())
    await wrapper.findAll('button').find(button => button.text() === 'Retry')!.trigger('click'); await flushPromises()
    expect(wrapper.get('h1').text()).toBe('Customer weekly review')
    expect(wrapper.find('ol').exists()).toBe(true)
  })
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

  it('retains open Details on query-only navigation and rejects a delayed result from another project', async () => {
    const { wrapper, router } = await open()
    ;(wrapper.get('details').element as HTMLDetailsElement).open = true
    const calls = client.apiFetch.mock.calls.length
    await router.push('/projects/1/plugins/engineering/workflows/engineering.tracked-delivery?inspection=retained'); await flushPromises()
    expect(client.apiFetch.mock.calls).toHaveLength(calls)
    expect((wrapper.get('details').element as HTMLDetailsElement).open).toBe(true)
    let resolve!: (value: unknown) => void
    client.apiFetch.mockImplementation((url: string) => {
      if (url.startsWith('/api/v1/plugins?')) return Promise.resolve([plugin()])
      if (url.includes('/projects/1/')) return new Promise(value => { resolve = value })
      const result = workflow()
      result.spec.name = 'Project two workflow'
      return Promise.resolve(result)
    })
    await wrapper.findAll('button').find(button => button.text() === 'Refresh')!.trigger('click'); await flushPromises()
    await router.push('/projects/2/plugins/engineering/workflows/engineering.tracked-delivery'); await flushPromises()
    resolve(workflow()); await flushPromises()
    expect(wrapper.get('h1').text()).toBe('Project two workflow')
    expect((wrapper.get('details').element as HTMLDetailsElement).open).toBe(false)
  })

  it('preserves the current workflow and disclosure when navigation is cancelled', async () => {
    const { wrapper, router } = await open()
    ;(wrapper.get('details').element as HTMLDetailsElement).open = true
    const calls = client.apiFetch.mock.calls.length
    router.beforeResolve(to => !to.path.includes('/support/'))
    await router.push('/projects/1/plugins/support/workflows/support.investigate'); await flushPromises()
    expect(router.currentRoute.value.params.pluginSlug).toBe('engineering')
    expect(client.apiFetch.mock.calls).toHaveLength(calls)
    expect(wrapper.get('h1').text()).toBe('Tracked delivery')
    expect((wrapper.get('details').element as HTMLDetailsElement).open).toBe(true)
  })
})
