import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import WorkflowDetailView from './WorkflowDetailView.vue'
import { openView, plugin, workflow } from './plugin-domain/testSupport'
import { ApiError } from '@/lib/client'

const client = vi.hoisted(() => ({ apiFetch: vi.fn() }))
vi.mock('@/lib/client', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/client')>(), apiFetch: client.apiFetch, formatApiError: (e: unknown) => String(e) }))
const wrappers: Awaited<ReturnType<typeof openView>>['wrapper'][] = []
async function open(path = '/projects/1/plugins/engineering/workflows/engineering.tracked-delivery') {
  const result = await openView(WorkflowDetailView, path)
  wrappers.push(result.wrapper); await flushPromises(); return result
}
describe('Workflow detail', () => {
  beforeEach(() => { client.apiFetch.mockReset().mockImplementation(async (url: string) => url.startsWith('/api/v1/plugins?') ? [plugin(), plugin('support')] : workflow()) })
  afterEach(() => { wrappers.forEach(wrapper => wrapper.unmount()); wrappers.length = 0; document.body.innerHTML = '' })
  it.each([
    '/projects/1/plugins/engineering/workflows/engineering.tracked-delivery',
    '/projects/1/workflows/customer.weekly-review',
  ])('uses layout-shaped initial structure without inventing stages or permission: %s', async path => {
    let resolve!: (value: unknown) => void
    client.apiFetch.mockImplementation((url: string) => url.startsWith('/api/v1/plugins?') ? Promise.resolve([plugin()]) : new Promise(value => { resolve = value }))
    const { wrapper } = await open(path)
    expect(wrapper.get('[aria-label="Loading workflow"]').findAll('.ui-skeleton').length).toBeGreaterThan(3)
    expect(wrapper.find('ol').exists()).toBe(false)
    expect(wrapper.text()).not.toMatch(/Start run|Ready for this project/)
    resolve(path.includes('/plugins/') ? workflow() : configuredWorkflow()); await flushPromises()
    expect(wrapper.find('[aria-label="Loading workflow"]').exists()).toBe(false)
    expect(wrapper.find('ol').exists()).toBe(true)
  })
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
    expect(wrapper.get('a').text()).toBe('Back to Home')
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
    expect(wrapper.get('a').text()).toBe('Back to workflows')
    expect(wrapper.get('a').attributes('href')).toBe('/projects/1/plugins/engineering')
    client.apiFetch.mockResolvedValue(configuredWorkflow('user.weekly-review'))
    await router.push('/projects/2/workflows/user.weekly-review'); await flushPromises()
    expect(client.apiFetch).toHaveBeenLastCalledWith('/api/v1/projects/2/workflow-templates/user.weekly-review')
    expect(wrapper.get('a').attributes('href')).toBe('/projects/2')
    expect(wrapper.get('h1').text()).toBe('Customer weekly review')
  })
  it.each([
    ['/projects/2/plugins/engineering/workflows/engineering.tracked-delivery', '/projects/2/plugins/engineering', 'Back to workflows'],
    ['/projects/2/workflows/customer.weekly-review', '/projects/2', 'Back to Home'],
  ])('keeps an explicit safe return destination on a direct link while loading and after failure: %s', async (path, destination, label) => {
    let reject!: (error: Error) => void
    client.apiFetch.mockReturnValue(new Promise((_resolve, fail) => { reject = fail }))
    const { wrapper, router } = await open(path)
    const back = wrapper.get('a')
    expect(wrapper.text()).toContain('Loading workflow')
    expect(back.text()).toBe(label)
    expect(back.attributes('href')).toBe(destination)
    expect(back.classes()).toContain('focus-ring')
    expect(back.classes()).toContain('ui-button')
    reject(new Error('Source unavailable')); await flushPromises()
    expect(wrapper.text()).toContain('Source unavailable')
    expect(wrapper.get('a').attributes('href')).toBe(destination)
    await wrapper.get('a').trigger('click'); await flushPromises()
    expect(router.currentRoute.value.path).toBe(destination)
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

  it.each([
    '/projects/1/plugins/engineering/workflows/engineering.tracked-delivery',
    '/projects/1/workflows/customer.weekly-review',
  ])('retains same-template content and disclosure after transient errors: %s', async path => {
    const initial = path.includes('/plugins/') ? workflow() : configuredWorkflow()
    client.apiFetch.mockImplementation(async (url: string) => url.startsWith('/api/v1/plugins?') ? [plugin()] : initial)
    const { wrapper } = await open(path)
    const stages = wrapper.get('ol').element
    const details = wrapper.get('details').element as HTMLDetailsElement
    details.open = true
    let reject!: (error: Error) => void
    client.apiFetch.mockImplementation((url: string) => url.startsWith('/api/v1/plugins?') ? Promise.resolve([plugin()]) : new Promise((_resolve, fail) => { reject = fail }))
    await wrapper.findAll('button').find(button => button.text() === 'Refresh')!.trigger('click'); await flushPromises()
    expect(wrapper.get('ol').element).toBe(stages)
    reject(new Error('Transient workflow outage')); await flushPromises()
    expect(wrapper.get('ol').element).toBe(stages)
    expect(details.open).toBe(true)
    expect(wrapper.text()).toContain('Last accepted workflow')
    expect(wrapper.text()).toContain('Transient workflow outage')
    expect(wrapper.text()).not.toMatch(/Ready for this project|Start run/)
    client.apiFetch.mockImplementation(async (url: string) => url.startsWith('/api/v1/plugins?') ? [plugin()] : initial)
    await wrapper.findAll('button').find(button => button.text() === 'Retry')!.trigger('click'); await flushPromises()
    expect(wrapper.get('ol').element).toBe(stages)
    expect(wrapper.text()).not.toContain('Transient workflow outage')
  })

  it.each([401, 403, 404, 422])('clears accepted content after confirmed HTTP %s', async status => {
    const { wrapper } = await open()
    client.apiFetch.mockImplementation(async (url: string) => {
      if (url.startsWith('/api/v1/plugins?')) return [plugin()]
      throw new ApiError('Unavailable', status, { detail: 'Workflow access unavailable' })
    })
    await wrapper.findAll('button').find(button => button.text() === 'Refresh')!.trigger('click'); await flushPromises()
    expect(wrapper.find('ol').exists()).toBe(false)
    expect(wrapper.get('h1').text()).toBe('Workflow')
    expect(wrapper.text()).toContain('Workflow access unavailable')
  })

  it('retains a catalog outage but clears on confirmed catalog denial and plugin disablement', async () => {
    const { wrapper } = await open()
    client.apiFetch.mockRejectedValue(new ApiError('Temporary', 503, { detail: 'Catalog outage' }))
    await wrapper.findAll('button').find(button => button.text() === 'Refresh')!.trigger('click'); await flushPromises()
    expect(wrapper.find('ol').exists()).toBe(true)
    expect(wrapper.text()).toContain('Last accepted workflow')
    client.apiFetch.mockRejectedValue(new ApiError('Denied', 403, { detail: 'Project denied' }))
    await wrapper.findAll('button').find(button => button.text() === 'Retry')!.trigger('click'); await flushPromises()
    expect(wrapper.find('ol').exists()).toBe(false)
    client.apiFetch.mockImplementation(async (url: string) => url.startsWith('/api/v1/plugins?') ? [plugin()] : workflow())
    await wrapper.findAll('button').find(button => button.text() === 'Retry')!.trigger('click'); await flushPromises()
    expect(wrapper.find('ol').exists()).toBe(true)
    client.apiFetch.mockResolvedValue([plugin('engineering', [], false)])
    await wrapper.findAll('button').find(button => button.text() === 'Refresh')!.trigger('click'); await flushPromises()
    expect(wrapper.find('ol').exists()).toBe(false)
    expect(wrapper.text()).toContain('disabled for this project')
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
