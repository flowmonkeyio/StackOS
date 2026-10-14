import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import FinanceView from './FinanceView.vue'
import { callOperation } from '@/lib/operations'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))
const snapshot = {
  revision: 1,
  sha256: 'a'.repeat(64),
  read_at: '2026-10-13',
  schema_version: 'local-json-v1',
  custody_issues: [],
}
const access = {
  project_id: 1,
  access_id: 'access:one',
  workspace_ref: 'finance:one',
  generation: 1,
  backend_key: 'local-json',
  modes: ['read', 'correct', 'export'],
  revoked_at: null,
  readiness: 'ready',
  snapshot,
}
function projection(args: Record<string, unknown>, vendor = 'Synthetic vendor') {
  const { project_id: _id, response_mode: _mode, ...query } = args
  return {
    snapshot,
    query,
    rows: [
      {
        record_ref: 'receipt:one',
        collection: 'receipts',
        kind: 'receipt',
        date: '2026-10-01',
        amount: { minor: '9007199254740993', currency: 'USD', exponent: 2 },
        counterparty: vendor,
        needs_review: true,
      },
    ],
    page: { total: 1, offset: 0, limit: 50, has_more: false },
    metrics_by_currency: [
      {
        currency: 'USD',
        exponent: 2,
        spend: {
          amount: { minor: '9007199254740993', currency: 'USD', exponent: 2 },
          state: 'partial',
          included_record_refs: ['receipt:one'],
        },
        revenue: { amount: null, state: 'unavailable', included_record_refs: [] },
        profit: { amount: null, state: 'unavailable', included_record_refs: [] },
      },
    ],
    breakdowns: { category: [], account: [], counterparty: [], project: [] },
    trend: [],
    review_items: [],
    coverage: { state: 'partial', issues: ['missing_source_window'] },
    exclusions: [],
    undated_or_period_only: ['receipt:undated'],
    warnings: ['prepared/unposted'],
    comparison: null,
  }
}
async function mounted() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/projects/:id/finance', component: FinanceView }],
  })
  await router.push('/projects/1/finance')
  await router.isReady()
  const wrapper = mount(
    { template: '<RouterView />' },
    {
      attachTo: document.body,
      global: {
        plugins: [router, createPinia()],
        stubs: {
          ProjectPageHeader: {
            template: '<header><h1>Finance</h1><slot name="actions" /></header>',
          },
          teleport: true,
        },
      },
    },
  )
  await flushPromises()
  return { wrapper, router }
}
beforeEach(() => {
  vi.mocked(callOperation).mockImplementation(async (name, args) => {
    if (name === 'financeWorkspace.status')
      return {
        data: {
          project_id: args.project_id,
          readiness: 'ready',
          access: [{ ...access, project_id: args.project_id }],
        },
      }
    if (name === 'finance.query') return { data: projection(args) }
    if (name === 'agentRequest.list') return { items: [], next_cursor: null, total_estimate: 0 }
    throw new Error(`Unexpected test operation ${name}`)
  })
})
afterEach(() => {
  vi.resetAllMocks()
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})
describe('Finance management', () => {
  it.each(['markdown', 'csv', 'json'])(
    'renders canonical current %s period bytes and private download',
    async (format) => {
      const revoke = vi.fn(),
        create = vi.fn(() => 'blob:period')
      const OriginalURL = URL
      vi.stubGlobal(
        'URL',
        class extends OriginalURL {
          static override createObjectURL = create
          static override revokeObjectURL = revoke
        },
      )
      const basics = vi.mocked(callOperation).getMockImplementation()!
      vi.mocked(callOperation).mockImplementation(async (name, args) =>
        name === 'finance.report.render'
          ? {
              state: 'current',
              snapshot,
              content_base64: btoa('Synthetic period content'),
              content_type: format === 'markdown' ? 'text/markdown' : 'text/plain',
              filename: `period.${format}`,
            }
          : basics(name, args),
      )
      const { wrapper } = await mounted()
      await wrapper
        .findAll('button')
        .find((b) => b.text() === 'Reports')!
        .trigger('click')
      const label = {
        markdown: 'View period report',
        csv: 'Prepare CSV export',
        json: 'Prepare JSON export',
      }[format]!
      await wrapper
        .findAll('button')
        .find((b) => b.text() === label)!
        .trigger('click')
      await flushPromises()
      expect(wrapper.get(`a[download="period.${format}"]`).attributes('href')).toBe('blob:period')
      if (format === 'markdown') expect(wrapper.text()).toContain('Synthetic period content')
      const query = vi.mocked(callOperation).mock.calls.find((c) => c[0] === 'finance.query')![1]
      expect(
        vi.mocked(callOperation).mock.calls.find((c) => c[0] === 'finance.report.render')![1],
      ).toMatchObject({
        ...query,
        format,
        report_kind: 'period',
        response_mode: 'raw',
        expected_snapshot: { revision: 1, sha256: snapshot.sha256 },
      })
      wrapper.unmount()
      expect(revoke).toHaveBeenCalledWith('blob:period')
    },
  )
  it.each(['unavailable', 'error', 'late-scope'])(
    'keeps %s period content unavailable',
    async (outcome) => {
      let resolve!: (v: unknown) => void
      const basics = vi.mocked(callOperation).getMockImplementation()!
      vi.mocked(callOperation).mockImplementation(async (name, args) => {
        if (name !== 'finance.report.render') return basics(name, args)
        if (outcome === 'error') throw new Error('Synthetic denial')
        if (outcome === 'late-scope')
          return new Promise((r) => {
            resolve = r
          })
        return { state: 'unavailable', content_base64: btoa('Unavailable private content') }
      })
      const { wrapper, router } = await mounted()
      await wrapper
        .findAll('button')
        .find((b) => b.text() === 'Reports')!
        .trigger('click')
      await wrapper
        .findAll('button')
        .find((b) => b.text() === 'View period report')!
        .trigger('click')
      await flushPromises()
      if (outcome === 'late-scope') {
        await router.push('/projects/2/finance')
        await flushPromises()
        resolve({
          state: 'current',
          content_base64: btoa('Old private report'),
          content_type: 'text/markdown',
          filename: 'old.md',
        })
        await flushPromises()
        expect(wrapper.text()).not.toContain('Old private report')
      } else expect(wrapper.text()).toContain('Report unavailable')
      expect(wrapper.find('a[download]').exists()).toBe(false)
      wrapper.unmount()
    },
  )
  it.each(['invoice-0001', '   '])(
    'refuses private-search %j analysis before queue and preserves scope until deliberate clear',
    async (search) => {
      let queued: Record<string, unknown> = {}
      const basics = vi.mocked(callOperation).getMockImplementation()!
      vi.mocked(callOperation).mockImplementation(async (name, args) => {
        if (name !== 'localAgentChat.createMessage') return basics(name, args)
        queued = (args.metadata_json as Record<string, unknown>).query as Record<string, unknown>
        if ('q' in queued) throw new Error('Private filter forbidden by accepted intake')
        return { data: { agent_request: { id: 7, status: 'new' } } }
      })
      const { wrapper } = await mounted()
      await wrapper.get('[data-test="finance-search"]').setValue(search)
      await wrapper.get('[data-test="apply-finance-filters"]').trigger('click')
      await flushPromises()
      const selected = vi
        .mocked(callOperation)
        .mock.calls.filter((c) => c[0] === 'finance.query')
        .at(-1)![1]
      const before = vi.mocked(callOperation).mock.calls.length
      await wrapper
        .findAll('button')
        .find((b) => b.text() === 'Request agent analysis')!
        .trigger('click')
      await flushPromises()
      expect(vi.mocked(callOperation).mock.calls.slice(before)).toHaveLength(0)
      expect(wrapper.text()).toContain('Clear search')
      expect((wrapper.get('[data-test="finance-search"]').element as HTMLInputElement).value).toBe(
        search,
      )
      await wrapper.get('[data-test="finance-search"]').setValue('')
      await wrapper.get('[data-test="apply-finance-filters"]').trigger('click')
      await flushPromises()
      const cleared = vi
        .mocked(callOperation)
        .mock.calls.filter((c) => c[0] === 'finance.query')
        .at(-1)![1]
      const { q: _q, project_id: _id, response_mode: _mode, ...remaining } = selected
      expect(cleared).toMatchObject(remaining)
      await wrapper
        .findAll('button')
        .find((b) => b.text() === 'Request agent analysis')!
        .trigger('click')
      await flushPromises()
      expect(queued).toEqual(remaining)
      expect(wrapper.text()).toContain('Awaiting agent')
      wrapper.unmount()
    },
  )
  it('queues safe selectors and renders bare reviewed results against the original window', async () => {
    let metadata: Record<string, unknown> = {}
    let state = 'run-started'
    vi.mocked(callOperation).mockImplementation(async (name, args) => {
      if (name === 'financeWorkspace.status')
        return { data: { project_id: 1, readiness: 'ready', access: [access] } }
      if (name === 'finance.query') return { data: projection(args) }
      if (name === 'agentRequest.list') return { items: [] }
      if (name === 'localAgentChat.createMessage') {
        metadata = args.metadata_json as Record<string, unknown>
        return {
          data: {
            agent_request: { id: 7, status: 'pending', metadata_json: { metadata_json: metadata } },
          },
        }
      }
      if (name === 'agentRequest.get')
        return {
          id: 7,
          status: 'completed',
          run_plan_id: null,
          metadata_json: { metadata_json: metadata },
        }
      if (name === 'finance.report.render')
        return { state, report: state === 'stale' ? 'Reviewed synthetic finding' : null }
      throw new Error(`Unexpected operation ${name}`)
    })
    const { wrapper } = await mounted()
    await wrapper
      .findAll('button')
      .find((button) => button.text() === 'Reports')!
      .trigger('click')
    await wrapper
      .findAll('button')
      .find((button) => button.text() === 'Request agent analysis')!
      .trigger('click')
    await flushPromises()
    expect(Object.keys(metadata).sort()).toEqual(
      [
        'access_generation',
        'analysis_kind',
        'intent_key',
        'query',
        'record_refs',
        'source_refs',
        'source_snapshot',
        'workspace_ref',
      ].sort(),
    )
    expect(wrapper.text()).toContain('Awaiting agent — a host must claim this request')
    await wrapper
      .findAll('button')
      .find((button) => button.text() === 'Refresh analysis status')!
      .trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Awaiting a linked run')
    expect(wrapper.text()).not.toContain('Running in the linked agent run')
    state = 'stale'
    await wrapper
      .findAll('button')
      .find((button) => button.text() === 'Refresh analysis status')!
      .trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Historical result: source changed')
    expect(wrapper.text()).toContain('Reviewed synthetic finding')
    const original = metadata.query as Record<string, unknown>
    expect(wrapper.text()).toContain(String(original.start))
    expect(
      vi
        .mocked(callOperation)
        .mock.calls.filter((c) => c[0] === 'finance.report.render')
        .at(-1)![1],
    ).toMatchObject({
      ...original,
      project_id: 1,
      workspace_ref: 'finance:one',
      access_generation: 1,
      analysis_request_id: 7,
      response_mode: 'raw',
    })
    expect(vi.mocked(callOperation).mock.calls.some((c) => c[0] === 'finance.record.commit')).toBe(
      false,
    )
    wrapper.unmount()
  })
  it('shows exact partial amounts, unknown revenue and retained undated refs', async () => {
    const { wrapper } = await mounted()
    expect(wrapper.text()).toContain('USD 90,071,992,547,409.93')
    expect(wrapper.text()).toContain('Unavailable')
    expect(wrapper.text()).toContain('receipt:undated')
    expect(wrapper.text()).toContain('Expenses & income')
    wrapper.unmount()
  })
  it('keeps selected dates during search and preserves all-time for historical lookup', async () => {
    const { wrapper } = await mounted()
    const search = wrapper.get('[data-test="finance-search"]')
    await search.setValue('invoice-0001')
    await wrapper.get('[data-test="apply-finance-filters"]').trigger('click')
    await flushPromises()
    const calls = vi.mocked(callOperation).mock.calls.filter((c) => c[0] === 'finance.query')
    expect(calls.at(-1)?.[1]).toMatchObject({
      q: 'invoice-0001',
      start: calls[0]?.[1].start,
      end: calls[0]?.[1].end,
    })
    const timeframe = wrapper
      .findAll('[role="combobox"]')
      .find((c) => c.text().includes('This month'))!
    await timeframe.trigger('click')
    await wrapper
      .findAll('[role="option"]')
      .find((o) => o.text() === 'All time')!
      .trigger('click')
    await wrapper.get('[data-test="apply-finance-filters"]').trigger('click')
    await flushPromises()
    expect(
      vi
        .mocked(callOperation)
        .mock.calls.filter((c) => c[0] === 'finance.query')
        .at(-1)?.[1],
    ).toMatchObject({ start: '0001-01-01', end: '9999-12-31', q: 'invoice-0001', comparison: null })
    wrapper.unmount()
  })
  it('rejects late query responses after a project scope change', async () => {
    let resolve!: (v: unknown) => void
    vi.mocked(callOperation).mockImplementation(async (name, args) => {
      if (name === 'financeWorkspace.status')
        return {
          data: {
            project_id: args.project_id,
            readiness: 'ready',
            access: [{ ...access, project_id: args.project_id }],
          },
        }
      if (name === 'finance.query' && args.project_id === 1)
        return new Promise((r) => {
          resolve = r
        })
      if (name === 'finance.query') return { data: projection(args, 'Second scope') }
      return { items: [] }
    })
    const { wrapper, router } = await mounted()
    await router.push('/projects/2/finance')
    await flushPromises()
    resolve({ data: projection({}, 'Old private vendor') })
    await flushPromises()
    expect(wrapper.text()).not.toContain('Old private vendor')
    expect(
      vi
        .mocked(callOperation)
        .mock.calls.some((c) => c[0] === 'finance.query' && c[1].project_id === 2),
    ).toBe(true)
    wrapper.unmount()
  })
  it('keeps failed loading explicit instead of showing stale totals', async () => {
    const { wrapper } = await mounted()
    vi.mocked(callOperation).mockRejectedValue(new Error('Synthetic denial'))
    await wrapper.get('[data-test="refresh-finance"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('unavailable')
    expect(wrapper.text()).not.toContain('USD 90,071,992,547,409.93')
    wrapper.unmount()
  })
  it('invalidates a pending query before refreshing workspace authority', async () => {
    let finishQuery!: (v: unknown) => void
    let statuses = 0
    vi.mocked(callOperation).mockImplementation(async (name) => {
      if (name === 'financeWorkspace.status') {
        if (++statuses > 1) return new Promise(() => {})
        return { data: { project_id: 1, readiness: 'ready', access: [access] } }
      }
      if (name === 'finance.query')
        return new Promise((r) => {
          finishQuery = r
        })
      return { items: [] }
    })
    const { wrapper } = await mounted()
    await wrapper.get('[data-test="refresh-finance"]').trigger('click')
    const old = projection({})
    old.metrics_by_currency[0]!.spend.amount.minor = '500000'
    finishQuery({ data: old })
    await flushPromises()
    expect(wrapper.text()).not.toContain('USD 5,000.00')
    wrapper.unmount()
  })
})
