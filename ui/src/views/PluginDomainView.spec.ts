import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import PluginDomainView from './PluginDomainView.vue'
import { openView, page, plugin, workflow } from './plugin-domain/testSupport'

const client = vi.hoisted(() => ({ apiFetch: vi.fn() }))
vi.mock('@/lib/client', () => ({ apiFetch: client.apiFetch, formatApiError: (e: unknown) => String(e) }))
const record = (id: number, key = 'engineering-decision', project_id = 1, plugin_slug = 'engineering') => ({ id, project_id, plugin_slug, resource_id: key === 'engineering-decision' ? 1 : 2, resource_key: key, title: `Same title ${id}`, data_json: { decision: `Exact saved decision ${id}` }, created_at: '2026-11-09' })
const wrappers: Awaited<ReturnType<typeof openView>>['wrapper'][] = []
async function open(path = '/projects/1/plugins/engineering') {
  const result = await openView(PluginDomainView, path)
  wrappers.push(result.wrapper)
  await flushPromises()
  return result
}
function button(wrapper: (typeof wrappers)[number], label: string) { return wrapper.findAll('button').find(button => button.text() === label)! }
function defaults(url: string) {
  if (url.startsWith('/api/v1/plugins?')) return [plugin()]
  if (url.includes('/workflow-templates?')) return { templates: [workflow().summary] }
  if (url.startsWith('/api/v1/resources?')) return [{ id: 1, plugin_slug: 'engineering', key: 'engineering-decision', name: 'Decision' }, { id: 2, plugin_slug: 'engineering', key: 'engineering-evidence', name: 'Evidence' }]
  return page([])
}
describe('Plugin domain', () => {
  beforeEach(() => { client.apiFetch.mockReset().mockImplementation(async (url: string) => defaults(url)) })
  afterEach(() => { wrappers.forEach(wrapper => wrapper.unmount()); wrappers.length = 0; document.body.innerHTML = '' })

  it('keeps declared empty sections, omits unsupported workflows and blocks disabled/missing plugins', async () => {
    const { wrapper, router } = await open()
    expect(wrapper.findAll('section[aria-label]').map(section => section.attributes('aria-label'))).toEqual(['Workflows', 'Saved information', 'Files'])
    expect(wrapper.text()).toContain('No saved information saved for this domain.')
    expect(wrapper.find('a[href$="/workflows/engineering.tracked-delivery"]').exists()).toBe(true)
    client.apiFetch.mockImplementation(async (url: string) => url.startsWith('/api/v1/plugins?') ? [plugin('publishing', ['saved', 'files'])] : defaults(url))
    await router.push('/projects/1/plugins/publishing'); await flushPromises()
    expect(wrapper.find('section[aria-label="Workflows"]').exists()).toBe(false)
    expect(client.apiFetch.mock.calls.some(([url]) => url.includes('workflow-templates?plugin_slug=publishing'))).toBe(false)
    client.apiFetch.mockImplementation(async () => [plugin('publishing', ['saved'], false)])
    await button(wrapper, 'Refresh').trigger('click'); await flushPromises()
    expect(wrapper.text()).toContain('disabled for this project')
    expect(wrapper.find('section').exists()).toBe(false)
    await router.push('/projects/1/plugins/missing'); await flushPromises()
    expect(wrapper.text()).toContain('unavailable for this project')
  })

  it('retains source identity, distinct resource types, paging and selection on refresh', async () => {
    client.apiFetch.mockImplementation(async (url: string) => url.includes('/resource-records?')
      ? url.includes('after=50') ? page([record(51, 'engineering-evidence')]) : page([record(1), record(2, 'engineering-evidence')], 50) : defaults(url))
    const { wrapper } = await open()
    const section = wrapper.get('section[aria-label="Saved information"]')
    expect(section.text()).toContain('Decision'); expect(section.text()).toContain('Evidence')
    await button(wrapper, 'Load more Saved information').trigger('click'); await flushPromises()
    await wrapper.get('[data-source="resource_record:51"]').trigger('keydown', { key: 'Enter' }); await flushPromises()
    expect(document.body.textContent).toContain('Exact saved decision 51')
    const disclosure = document.body.querySelector('.ui-sidepanel__body details') as HTMLDetailsElement
    disclosure.open = true
    document.body.querySelector('.ui-sidepanel__body')!.scrollTop = 90
    await button(wrapper, 'Refresh').trigger('click'); await flushPromises()
    expect(document.body.textContent).toContain('Exact saved decision 51')
    expect(client.apiFetch.mock.calls.filter(([url]) => url.includes('/resource-records?') && !url.includes('after='))).toHaveLength(2)
    expect(client.apiFetch.mock.calls.filter(([url]) => url.includes('/artifacts?'))).toHaveLength(2)
    expect((document.body.querySelector('.ui-sidepanel__body details') as HTMLDetailsElement).open).toBe(true)
    expect(document.body.querySelector('.ui-sidepanel__body')!.scrollTop).toBe(90)
    expect(wrapper.find('[data-source="resource_record:51"]').exists()).toBe(true)
    expect(client.apiFetch.mock.calls.filter(([url]) => url.includes('/resource-records?')).every(([url]) => url.includes('plugin_slug=engineering'))).toBe(true)
    expect(wrapper.findAll('a').filter(a => a.text().startsWith('Open in Library')).map(a => a.attributes('href'))).toEqual(['/projects/1/library?tab=saved&plugin=engineering', '/projects/1/library?tab=files&plugin=engineering'])
  })

  it('retains the workflow list during a compatible delayed refresh', async () => {
    const { wrapper } = await open()
    let resolve!: (value: unknown) => void
    client.apiFetch.mockImplementation((url: string) => url.includes('/workflow-templates?') ? new Promise(value => { resolve = value }) : Promise.resolve(defaults(url)))
    await button(wrapper, 'Refresh').trigger('click'); await flushPromises()
    expect(wrapper.get('section[aria-label="Workflows"]').text()).toContain('Tracked delivery')
    expect(wrapper.text()).not.toContain('No workflows available')
    resolve({ templates: [workflow().summary] }); await flushPromises()
  })

  it.each(['Enter', ' '])('retains source inspection after %j default activation when focus enters the drawer', async key => {
    client.apiFetch.mockImplementation(async (url: string) => url.includes('/resource-records?') ? page([record(1)]) : defaults(url))
    const { wrapper } = await open()
    const opener = wrapper.get('[data-source="resource_record:1"]').element as HTMLButtonElement
    opener.focus()
    const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true })
    opener.dispatchEvent(event)
    await flushPromises()
    // JSDOM does not synthesize button defaults. Reproduce the uncancelled
    // default click caught by native browser QA after the drawer moves focus.
    const close = document.body.querySelector<HTMLButtonElement>('button[aria-label="Close panel"]')!
    expect(document.activeElement).toBe(close)
    if (!event.defaultPrevented) (document.activeElement as HTMLButtonElement).click()
    await flushPromises()
    expect(document.body.querySelector('[role="dialog"]')?.textContent).toContain('Exact saved decision 1')
    expect(event.defaultPrevented).toBe(true)
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
    await flushPromises()
    expect(document.body.querySelector('[role="dialog"]')).toBeNull()
    expect(document.activeElement).toBe(opener)
  })

  it('keeps query-only navigation local and clears filtered-out selection through both controls', async () => {
    client.apiFetch.mockImplementation(async (url: string) => url.includes('/resource-records?') ? page([record(1), record(2, 'engineering-evidence')]) : defaults(url))
    const { wrapper, router } = await open()
    expect(client.apiFetch.mock.calls.filter(([url]) => url.includes('/resource-records?'))).toHaveLength(1)
    expect(client.apiFetch.mock.calls.filter(([url]) => url.includes('/artifacts?'))).toHaveLength(1)
    await wrapper.get('[data-source="resource_record:1"]').trigger('click'); await flushPromises()
    const calls = client.apiFetch.mock.calls.length
    await router.push('/projects/1/plugins/engineering?inspection=retained'); await flushPromises()
    expect(client.apiFetch.mock.calls).toHaveLength(calls)
    expect(document.body.textContent).toContain('Exact saved decision 1')
    wrapper.findComponent({ name: 'UiSelect' }).vm.$emit('update:modelValue', 'engineering-evidence'); await flushPromises()
    expect(document.body.querySelector('[role="dialog"]')).toBeNull()
    expect(wrapper.findAll('a').some(a => a.attributes('href')?.includes('type=engineering-evidence'))).toBe(true)
    await wrapper.get('[data-source="resource_record:2"]').trigger('click'); await flushPromises()
    await wrapper.get('input[placeholder="Search this list"]').setValue('no match'); await flushPromises()
    expect(document.body.querySelector('[role="dialog"]')).toBeNull()
    expect(wrapper.text()).toContain('No matches in the loaded list.')
  })

  it('ignores a delayed workflow list after domain navigation and reloads the prior domain on back', async () => {
    let resolve!: (value: unknown) => void
    client.apiFetch.mockImplementation((url: string) => {
      if (url.startsWith('/api/v1/plugins?')) return Promise.resolve([plugin(), plugin('publishing', ['saved', 'files'])])
      if (url.includes('workflow-templates?plugin_slug=engineering')) return new Promise(value => { resolve = value })
      return Promise.resolve(defaults(url))
    })
    const { wrapper, router } = await open()
    const finishOldList = resolve
    await router.push('/projects/1/plugins/publishing'); await flushPromises()
    finishOldList({ templates: [workflow().summary] }); await flushPromises()
    expect(wrapper.get('h1').text()).toBe('publishing')
    expect(wrapper.find('section[aria-label="Workflows"]').exists()).toBe(false)
    router.back(); await flushPromises()
    resolve({ templates: [workflow().summary] }); await flushPromises()
    expect(wrapper.get('h1').text()).toBe('Engineering')
    expect(wrapper.get('section[aria-label="Workflows"]').text()).toContain('Tracked delivery')
  })

  it('keeps the current domain and selection when a later navigation guard cancels a scope change', async () => {
    client.apiFetch.mockImplementation(async (url: string) => url.includes('/resource-records?') ? page([record(1)]) : defaults(url))
    const { wrapper, router } = await open()
    await wrapper.get('[data-source="resource_record:1"]').trigger('click'); await flushPromises()
    const calls = client.apiFetch.mock.calls.length
    router.beforeResolve(to => !to.path.endsWith('/publishing'))
    await router.push('/projects/1/plugins/publishing'); await flushPromises()
    expect(router.currentRoute.value.params.pluginSlug).toBe('engineering')
    expect(client.apiFetch.mock.calls).toHaveLength(calls)
    expect(wrapper.get('h1').text()).toBe('Engineering')
    expect(document.body.textContent).toContain('Exact saved decision 1')
  })

  it('shows failed reads, retries the section, scopes types and Library links, and rejects late project data', async () => {
    let fail = true
    let resolve!: (value: unknown) => void
    client.apiFetch.mockImplementation(async (url: string) => {
      if (url.includes('/resource-records?')) {
        if (url.includes('/projects/2/')) return page([record(2, 'engineering-evidence', 2)])
        if (fail) { fail = false; throw new Error('Records unavailable') }
        return page([record(1)])
      }
      return defaults(url)
    })
    const { wrapper, router } = await open()
    expect(wrapper.text()).toContain('Records unavailable')
    expect(wrapper.text()).not.toContain('No saved information saved')
    await button(wrapper, 'Retry Saved information').trigger('click'); await flushPromises()
    expect(wrapper.find('[data-source="resource_record:1"]').exists()).toBe(true)
    const select = wrapper.findComponent({ name: 'UiSelect' })
    select.vm.$emit('update:modelValue', 'engineering-decision'); await flushPromises()
    expect(wrapper.find('a[href*="type=engineering-decision"]').exists()).toBe(true)
    client.apiFetch.mockImplementation((url: string) => url.includes('/projects/1/resource-records?') ? new Promise(value => { resolve = value }) : Promise.resolve(url.includes('/projects/2/resource-records?') ? page([record(2, 'engineering-evidence', 2)]) : defaults(url)))
    await button(wrapper, 'Refresh').trigger('click'); await flushPromises()
    await router.push('/projects/2/plugins/engineering'); await flushPromises()
    resolve(page([record(99)])); await flushPromises()
    expect(wrapper.find('[data-source="resource_record:99"]').exists()).toBe(false)
    expect(wrapper.find('[data-source="resource_record:2"]').exists()).toBe(true)
  })
})
