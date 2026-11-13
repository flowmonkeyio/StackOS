import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'

import PatternsView from './PatternsView.vue'
import { ApiError } from '@/lib/client'
import type { PatternDetail, PatternSummary } from '@/composables/usePatterns'

const operation = vi.hoisted(() => vi.fn())
vi.mock('@/lib/operations', () => ({ callOperation: operation }))
const entry = (id = 1, extra: Partial<PatternDetail> = {}): PatternDetail => ({
  id,
  key: `fixture-${id}`,
  title: `Guidance ${id}`,
  summary: 'When this applies',
  tags: ['arbitrary-topic'],
  etag: `${id}:1`,
  source: 'custom',
  source_label: 'Custom',
  updated_at: '2026-11-12T12:00:00Z',
  body: 'Use [Vue](https://vuejs.org/guide/).\n\n```ts\nconst count = ref(0)\n```\n<script>bad()</script>',
  ...extra,
})
const page = (
  items: PatternSummary[] = [],
  next_cursor: number | null = null,
  catalog_total = items.length,
) => ({ items, next_cursor, total_estimate: catalog_total, catalog_total })
const wrappers: VueWrapper[] = []
async function open(path = '/patterns') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/patterns/:patternId?', component: PatternsView },
      { path: '/elsewhere', component: { template: '<p>Elsewhere</p>' } },
    ],
  })
  await router.push(path)
  await router.isReady()
  const wrapper = mount(
    { template: '<RouterView />' },
    { attachTo: document.body, global: { plugins: [router] } },
  )
  wrappers.push(wrapper)
  await flushPromises()
  return { router, wrapper }
}
function button(text: string, root: ParentNode = document.body) {
  const target = [...root.querySelectorAll<HTMLButtonElement>('button')].find(
    (x) => x.textContent?.trim() === text,
  )
  if (!target) throw new Error(`Missing button: ${text}`)
  return target
}
async function click(text: string, root?: ParentNode) {
  button(text, root).click()
  await flushPromises()
}
async function field(label: string, value: string) {
  const labelNode = [...document.querySelectorAll('label')].find(
    (x) => x.textContent?.trim().replace('*', '').trim() === label,
  )!
  const input = document.getElementById(labelNode.htmlFor) as HTMLInputElement
  input.value = value
  input.dispatchEvent(new Event('input', { bubbles: true }))
  await flushPromises()
}
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((a, b) => {
    resolve = a
    reject = b
  })
  return { promise, resolve, reject }
}

describe('Patterns', () => {
  beforeEach(() =>
    operation
      .mockReset()
      .mockImplementation(async (name) => (name === 'pattern.list' ? page() : entry())),
  )
  afterEach(() => {
    wrappers.forEach((w) => w.unmount())
    wrappers.length = 0
    document.body.innerHTML = ''
    vi.restoreAllMocks()
  })

  it('keeps loading, empty catalog, no matches and request failure distinct with retry', async () => {
    const pending = deferred<ReturnType<typeof page>>()
    operation.mockReturnValueOnce(pending.promise)
    const { wrapper } = await open()
    expect(wrapper.text()).toContain('Loading patterns')
    pending.resolve(page())
    await flushPromises()
    expect(wrapper.text()).toContain('No patterns yet')
    operation.mockResolvedValueOnce(page([], null, 2))
    await wrapper.get('input[type="search"]').setValue('missing')
    await flushPromises()
    expect(wrapper.text()).toContain('No matching patterns')
    operation.mockRejectedValueOnce(new Error('Offline'))
    await wrapper.get('input[type="search"]').setValue('retry')
    await flushPromises()
    expect(wrapper.text()).toContain('Could not load patterns')
    expect(wrapper.text()).not.toContain('No patterns yet')
    operation.mockResolvedValueOnce(page([entry()]))
    await click('Retry')
    expect(wrapper.text()).toContain('Guidance 1')
  })

  it('combines text, any-tag and source in the shared bounded query and resets paging', async () => {
    operation.mockResolvedValue(page([entry()], 10, 40))
    const { wrapper } = await open()
    await click('Next')
    expect(operation.mock.lastCall?.[1]).toMatchObject({ cursor: 10, limit: 25 })
    await wrapper.get('input[type="search"]').setValue('article text')
    await click('arbitrary-topic')
    await wrapper.get('[aria-label="Pattern source"]').trigger('click')
    await click('StackOS')
    expect(operation.mock.lastCall?.[1]).toEqual({
      query: 'article text',
      tags: ['arbitrary-topic'],
      source: 'stackos',
      cursor: null,
      limit: 25,
    })
    expect(button('Previous').disabled).toBe(true)
    await wrapper.get('[aria-label="Remove tag arbitrary-topic"]').trigger('click')
    expect(operation.mock.lastCall?.[1].tags).toEqual([])
    await click('Clear filters')
    expect(operation.mock.lastCall?.[1]).toMatchObject({
      query: null,
      tags: [],
      source: 'all',
      cursor: null,
    })
  })

  it('renders all three server-derived source labels and selectively loads sanitized Markdown detail', async () => {
    operation.mockImplementation(async (name) =>
      name === 'pattern.list'
        ? page([
            entry(),
            entry(2, { source: 'stackos', source_label: 'StackOS' }),
            entry(3, { source: 'customized', source_label: 'StackOS · Customized' }),
          ])
        : entry(),
    )
    const { wrapper, router } = await open()
    expect(wrapper.text()).toContain('StackOS · Customized')
    expect(wrapper.text()).toContain('Custom')
    expect(operation.mock.calls.map((x) => x[0])).toEqual(['pattern.list'])
    await router.push('/patterns/1')
    await flushPromises()
    expect(operation.mock.lastCall?.slice(0, 2)).toEqual(['pattern.get', { pattern_id: 1 }])
    expect(wrapper.get('article a').attributes('href')).toBe('https://vuejs.org/guide/')
    expect(wrapper.get('article pre code').text()).toContain('const count = ref(0)')
    expect(wrapper.find('article script').exists()).toBe(false)
    await click('Back to Patterns')
    expect(router.currentRoute.value.path).toBe('/patterns')
  })

  it('distinguishes missing detail from failed detail and ignores late list/detail responses', async () => {
    const oldList = deferred<ReturnType<typeof page>>()
    const oldDetail = deferred<PatternDetail>()
    operation.mockImplementation((name, args) => {
      if (name === 'pattern.list')
        return args.query ? Promise.resolve(page([entry(2)])) : oldList.promise
      return args.pattern_id === 1 ? oldDetail.promise : Promise.resolve(entry(2))
    })
    const { wrapper, router } = await open()
    await wrapper.get('input[type="search"]').setValue('new')
    oldList.resolve(page([entry(1)]))
    await flushPromises()
    expect(wrapper.text()).toContain('Guidance 2')
    expect(wrapper.text()).not.toContain('Guidance 1')
    await router.push('/patterns/1')
    await router.push('/patterns/2')
    oldDetail.resolve(entry(1))
    await flushPromises()
    expect(wrapper.get('h1').text()).toBe('Guidance 2')
    operation.mockRejectedValueOnce(new ApiError('Missing', 404, { detail: 'Missing' }))
    await router.push('/patterns/3')
    await flushPromises()
    expect(wrapper.text()).toContain('Pattern not found')
    operation.mockRejectedValueOnce(new Error('Unavailable'))
    await router.push('/patterns/4')
    await flushPromises()
    expect(wrapper.text()).toContain('Could not load pattern')
    expect(wrapper.text()).not.toContain('Pattern not found')
  })

  it('retains a failed save draft, previews Markdown and creates through the shared envelope', async () => {
    const { wrapper, router } = await open()
    await click('Add pattern')
    await field('Title', 'Synthetic example')
    await field('Summary', 'Useful for a specific task')
    await field('Tags', 'flexible, Vue')
    await field('Markdown guidance', entry().body)
    await click('Preview Markdown')
    expect(document.querySelector('[aria-label="Markdown preview"] a')?.getAttribute('href')).toBe(
      'https://vuejs.org/guide/',
    )
    operation.mockImplementation(async (name) => {
      if (name === 'pattern.upsert') throw new Error('Save unavailable')
      return page()
    })
    document
      .getElementById('pattern-editor')!
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await flushPromises()
    expect(document.body.textContent).toContain('Save unavailable')
    expect((document.querySelector('#pattern-editor input') as HTMLInputElement).value).toBe(
      'Synthetic example',
    )
    operation.mockImplementation(async (name) =>
      name === 'pattern.upsert'
        ? { data: entry() }
        : name === 'pattern.get'
          ? entry()
          : page([entry()]),
    )
    document
      .getElementById('pattern-editor')!
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/patterns/1')
    const saves = operation.mock.calls.filter((x) => x[0] === 'pattern.upsert')
    expect(saves[1][1]).toMatchObject({ title: 'Synthetic example', tags: ['flexible', 'Vue'] })
    expect(saves[1][1]).not.toHaveProperty('expected_etag')
    expect(wrapper.get('h1').text()).toBe('Guidance 1')
  })

  it('requires discard confirmation for close and navigation, and copies the etag on edits', async () => {
    const { router } = await open('/patterns/1')
    await click('Edit pattern')
    await field('Title', 'Changed')
    await click('Cancel', document.getElementById('pattern-editor')!.closest('[role="dialog"]')!)
    expect(document.body.textContent).toContain('Discard changes?')
    await click('Cancel', document.querySelectorAll('[role="dialog"]')[1])
    expect(document.getElementById('pattern-editor')).not.toBeNull()
    const navigation = router.push('/elsewhere')
    await flushPromises()
    await click('Cancel', document.querySelectorAll('[role="dialog"]')[1])
    await navigation
    expect(router.currentRoute.value.path).toBe('/patterns/1')
    operation.mockImplementation(async (name) =>
      name === 'pattern.upsert'
        ? { data: entry() }
        : name === 'pattern.get'
          ? entry()
          : page([entry()]),
    )
    document
      .getElementById('pattern-editor')!
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await flushPromises()
    expect(operation.mock.calls.find((x) => x[0] === 'pattern.upsert')?.[1]).toMatchObject({
      key: 'fixture-1',
      title: 'Changed',
      expected_etag: '1:1',
    })
    await click('Edit pattern')
    await field('Title', 'Discard me')
    const leaving = router.push('/elsewhere')
    await flushPromises()
    await click('Discard changes')
    await leaving
    expect(router.currentRoute.value.path).toBe('/elsewhere')
  })

  it('names the app-wide bundled deletion in the real dialog body; cancel is inert and failure retries', async () => {
    operation.mockImplementation(async (name) =>
      name === 'pattern.list'
        ? page([entry()])
        : entry(1, { source: 'stackos', source_label: 'StackOS' }),
    )
    const { router } = await open('/patterns/1')
    await click('Delete pattern')
    const dialog = document.querySelector('[role="dialog"]')!
    expect(dialog.textContent).toContain('Guidance 1')
    expect(dialog.textContent).toContain('every project in this installation')
    expect(dialog.textContent).toContain('persists across software updates')
    expect(dialog.querySelector('.ui-dialog__body')?.textContent ?? dialog.textContent).toContain(
      'Guidance 1',
    )
    await click('Cancel', dialog)
    expect(operation.mock.calls.some((x) => x[0] === 'pattern.delete')).toBe(false)
    await click('Delete pattern')
    operation.mockRejectedValueOnce(new Error('Delete unavailable'))
    await click('Delete pattern', document.querySelector('[role="dialog"]')!)
    expect(document.body.textContent).toContain('Delete unavailable')
    operation.mockImplementation(async (name) =>
      name === 'pattern.delete' ? { data: entry() } : page(),
    )
    await click('Delete pattern', document.querySelector('[role="dialog"]')!)
    expect(router.currentRoute.value.path).toBe('/patterns')
    expect(operation.mock.calls.filter((x) => x[0] === 'pattern.delete').at(-1)?.[1]).toEqual({
      pattern_id: 1,
      expected_etag: '1:1',
    })
    expect(document.body.textContent).toContain('No patterns yet')
  })

  it('explains revision conflicts in operator language while retaining the complete draft', async () => {
    await open('/patterns/1')
    await click('Edit pattern')
    await field('Title', 'Kept local draft')
    operation.mockRejectedValueOnce(new ApiError('Call pattern.get', 409, { detail: 'Call pattern.get' }))
    document.getElementById('pattern-editor')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await flushPromises()
    expect(document.body.textContent).toContain('This pattern changed elsewhere. Your edits are kept.')
    expect(document.body.textContent).not.toContain('Call pattern.get')
    expect((document.querySelector('#pattern-editor input') as HTMLInputElement).value).toBe('Kept local draft')
  })

  it('does not navigate or reload after a save settles beyond the owning component lifetime', async () => {
    const pending = deferred<{ data: PatternSummary }>()
    const { wrapper, router } = await open()
    await click('Add pattern')
    await field('Title', 'Pending example')
    await field('Markdown guidance', 'Example usage')
    operation.mockReturnValueOnce(pending.promise)
    document
      .getElementById('pattern-editor')!
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await flushPromises()
    wrapper.unmount()
    const retainedPath = router.currentRoute.value.path
    const count = operation.mock.calls.length
    pending.resolve({ data: entry() })
    await flushPromises()
    expect(operation.mock.calls).toHaveLength(count)
    expect(router.currentRoute.value.path).toBe(retainedPath)
  })
})
