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
  const query = { ...args }
  delete query.project_id
  delete query.response_mode
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
async function mounted(query = '') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/projects/:id/finance', component: FinanceView }],
  })
  await router.push('/projects/1/finance' + query)
  await router.isReady()
  const wrapper = mount(
    {
      template:
        '<RouterView v-slot="{ Component }"><component :is="Component" :key="$route.params.id" /></RouterView>',
    },
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
  it.each([
    ['available', 'Available · 2'],
    ['unavailable', 'Unavailable · 2'],
    ['partial', 'Partial · 1/2 available'],
    ['not_verified', 'Not verified · 2'],
    ['not_linked', 'Not linked'],
    ['unidentified', 'Needs identification'],
    ['legacy', 'Not verified'],
  ])(
    'shows separate %s document columns and keeps exact sourced money',
    async (state, expected) => {
      const basics = vi.mocked(callOperation).getMockImplementation()!
      const count = ['not_linked', 'unidentified'].includes(state) ? 0 : 2
      const invoiceSummary = {
        state,
        document_count: count,
        available_count: 1,
        unavailable_count: 1,
        not_verified_count: 0,
      }
      const receiptSummary = {
        state: 'available',
        document_count: 1,
        available_count: 1,
        unavailable_count: 0,
        not_verified_count: 0,
      }
      vi.mocked(callOperation).mockImplementation(async (name, args) => {
        if (name !== 'finance.query') return basics(name, args)
        const result = projection(args)
        return {
          data: {
            ...result,
            rows: result.rows.map((row) => ({
              ...row,
              ...(state === 'legacy'
                ? {}
                : { document_summary: { invoice: invoiceSummary, receipt: receiptSummary } }),
            })),
          },
        }
      })
      const { wrapper } = await mounted()
      await wrapper
        .findAll('button')
        .find((button) => button.text() === 'Expenses & income')!
        .trigger('click')
      const table = wrapper.get('table[aria-label="Finance records"]')
      expect(table.findAll('th').map((cell) => cell.text())).toContain('Invoice')
      expect(table.findAll('th').map((cell) => cell.text())).toContain('Receipt')
      const cells = table
        .get('tbody tr')
        .findAll('td')
        .map((cell) => cell.text())
      expect(cells[6]).toBe(expected)
      expect(cells[7]).toBe(state === 'legacy' ? 'Not verified' : 'Available · 1')
      expect(cells).toContain('USD 90,071,992,547,409.93')
      expect(wrapper.text()).toContain('File availability alone does not confirm payment')
      expect(
        vi.mocked(callOperation).mock.calls.filter(([name]) => name === 'finance.query'),
      ).toHaveLength(1)
      wrapper.unmount()
    },
  )
  it('opens document detail from a keyboard-selected record through the shared table', async () => {
    const basics = vi.mocked(callOperation).getMockImplementation()!
    vi.mocked(callOperation).mockImplementation(async (name, args) =>
      name === 'finance.record.get'
        ? {
            data: {
              requested_ref: 'receipt:one',
              current_ref: 'receipt:one',
              collection: 'receipts',
              record: { business_date: '2026-10-01' },
              original: {},
              source_refs: [],
              evidence: [],
              lineage: [],
              editable_fields: [],
              provider_fields_editable: false,
              document_summary: null,
            },
          }
        : basics(name, args),
    )
    const { wrapper } = await mounted()
    await wrapper
      .findAll('button')
      .find((button) => button.text() === 'Expenses & income')!
      .trigger('click')
    const row = wrapper.get('table[aria-label="Finance records"] tbody tr')
    expect(row.attributes('tabindex')).toBe('0')
    await row.trigger('keydown', { key: 'Enter' })
    await flushPromises()
    expect(
      vi.mocked(callOperation).mock.calls.find(([name]) => name === 'finance.record.get')![1],
    ).toMatchObject({
      record_ref: 'receipt:one',
      project_id: 1,
      workspace_ref: 'finance:one',
      access_generation: 1,
      expected_snapshot: { revision: 1, sha256: snapshot.sha256 },
    })
    expect(wrapper.get('section[aria-label="Original documents"]').text()).toContain(
      'Invoice · Not verified',
    )
    wrapper.unmount()
  })
  it('keeps an explicitly revoked workspace selected and clears private content until project change', async () => {
    let revoked = false
    const revokeBlob = vi.fn()
    const OriginalURL = URL
    vi.stubGlobal(
      'URL',
      class extends OriginalURL {
        static override createObjectURL = vi.fn(() => 'blob:revoked-report')
        static override revokeObjectURL = revokeBlob
      },
    )
    const basics = vi.mocked(callOperation).getMockImplementation()!
    vi.mocked(callOperation).mockImplementation(async (name, args) => {
      if (name === 'financeWorkspace.status') {
        const other = {
          ...access,
          project_id: args.project_id,
          workspace_ref: 'finance:other',
          readiness: 'ready',
        }
        return {
          data: {
            project_id: args.project_id,
            readiness: args.project_id === 1 ? 'select_workspace' : 'ready',
            access:
              args.project_id === 1
                ? [
                    {
                      ...access,
                      readiness: revoked ? 'revoked' : 'ready',
                      generation: revoked ? 2 : 1,
                      revoked_at: revoked ? '2026-10-14T08:00:00Z' : null,
                    },
                    other,
                  ]
                : [other],
          },
        }
      }
      if (name === 'financeWorkspace.revoke') {
        revoked = true
        return { data: { ...access, readiness: 'revoked', generation: 2 } }
      }
      if (name === 'finance.report.render')
        return {
          state: 'current',
          snapshot,
          content_base64: btoa('Private report from revoked workspace'),
          content_type: 'text/markdown',
          filename: 'revoked.md',
        }
      return basics(name, args)
    })
    const { wrapper, router } = await mounted()
    expect(wrapper.text()).toContain('USD 90,071,992,547,409.93')
    await wrapper
      .findAll('button')
      .find((button) => button.text() === 'Reports')!
      .trigger('click')
    await wrapper
      .findAll('button')
      .find((button) => button.text() === 'View period report')!
      .trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Private report from revoked workspace')
    expect(wrapper.find('a[download="revoked.md"]').exists()).toBe(true)
    await wrapper
      .findAll('button')
      .find((button) => button.text() === 'Workspace access')!
      .trigger('click')
    const priorQueries = vi
      .mocked(callOperation)
      .mock.calls.filter(([name]) => name === 'finance.query').length
    await wrapper
      .findAll('button')
      .find((button) => button.text() === 'Revoke current access')!
      .trigger('click')
    await flushPromises()
    expect(
      vi.mocked(callOperation).mock.calls.filter(([name]) => name === 'finance.query'),
    ).toHaveLength(priorQueries)
    expect(wrapper.get('[role="alert"]').text()).toContain(
      'Finance workspace authorization was revoked.',
    )
    expect(wrapper.text()).not.toContain('Private report from revoked workspace')
    expect(wrapper.text()).not.toContain('USD 90,071,992,547,409.93')
    expect(wrapper.find('a[download]').exists()).toBe(false)
    expect(revokeBlob).toHaveBeenCalledWith('blob:revoked-report')
    expect(
      vi.mocked(callOperation).mock.calls.find(([name]) => name === 'financeWorkspace.revoke')![1],
    ).toMatchObject({ project_id: 1, workspace_ref: 'finance:one', expected_generation: 1 })
    await wrapper.get('[data-test="refresh-finance"]').trigger('click')
    await flushPromises()
    expect(
      vi.mocked(callOperation).mock.calls.filter(([name]) => name === 'finance.query'),
    ).toHaveLength(priorQueries)
    expect(wrapper.get('[role="alert"]').text()).toContain(
      'Finance workspace authorization was revoked.',
    )
    expect(
      vi.mocked(callOperation).mock.calls.some(([name]) => name === 'financeWorkspace.select'),
    ).toBe(false)
    await router.push('/projects/2/finance')
    await flushPromises()
    expect(
      vi
        .mocked(callOperation)
        .mock.calls.filter(([name]) => name === 'finance.query')
        .slice(priorQueries)
        .map(([, args]) => [args.project_id, args.workspace_ref]),
    ).toEqual([[2, 'finance:other']])
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Private report from revoked workspace')
    wrapper.unmount()
  })
  it('explains zero supported revenue contributions while expense-only totals stay unavailable', async () => {
    const basics = vi.mocked(callOperation).getMockImplementation()!
    vi.mocked(callOperation).mockImplementation(async (name, args) => {
      if (name !== 'finance.query') return basics(name, args)
      const contribution = {
        label: 'Unknown/unassigned',
        currency: 'USD',
        spend_minor: '9007199254740993',
        revenue_minor: '0',
        included_record_refs: ['book:expense'],
      }
      return {
        data: {
          ...projection(args),
          breakdowns: {
            category: [contribution],
            account: [{ ...contribution, label: 'bank:one' }],
            counterparty: [contribution],
            project: [contribution],
          },
          trend: [{ ...contribution, date: '2026-10-01' }],
        },
      }
    })
    const { wrapper } = await mounted()
    const metrics = wrapper.findAllComponents({ name: 'UiMetricCard' })
    for (const label of ['Revenue', 'Operating profit']) {
      const metric = metrics.find((card) => card.props('label') === label)!
      expect(metric.props('value')).toBe('Unavailable')
      expect(metric.props('hint')).toBe('unavailable')
    }
    for (const label of [
      'category breakdown',
      'account breakdown',
      'counterparty breakdown',
      'project breakdown',
      'Finance trend',
    ]) {
      const table = wrapper.get(`table[aria-label="${label}"]`)
      expect(table.findAll('th').map((header) => header.text())).toContain('Spend contribution')
      expect(table.findAll('th').map((header) => header.text())).toContain('Revenue contribution')
      expect(table.text()).toContain('USD 90,071,992,547,409.93')
      expect(table.text()).toContain('USD 0.00')
    }
    const note = wrapper
      .findAll('p')
      .find((paragraph) => paragraph.text().startsWith('Breakdowns and trends show'))!
      .text()
    expect(note).toContain('selected supported records')
    expect(note).toContain(
      'A zero revenue contribution means no selected supported income contribution',
    )
    expect(note).toContain(
      'for expense-only records without income coverage, total revenue and operating profit remain unavailable',
    )
    wrapper.unmount()
  })
  it.each([
    ['not_configured', 'The selected finance workspace is missing finance.json.'],
    ['unreadable', 'The selected finance workspace cannot be read.'],
    ['corrupt_workspace', 'The selected finance workspace is corrupt or invalid.'],
    ['unsupported_version', 'The selected finance workspace uses an unsupported version.'],
    ['revoked', 'Finance workspace authorization was revoked.'],
    ['denied', 'Finance workspace access was denied.'],
    ['root_changed', 'The selected finance workspace folder changed.'],
    ['unknown-private-diagnostic', 'Finance workspace is not ready.'],
  ])(
    'explains %s readiness and clears prior private content without switching workspaces',
    async (readiness, expected) => {
      let failed = false
      const revoke = vi.fn()
      const OriginalURL = URL
      vi.stubGlobal(
        'URL',
        class extends OriginalURL {
          static override createObjectURL = vi.fn(() => 'blob:prior-finance-report')
          static override revokeObjectURL = revoke
        },
      )
      const basics = vi.mocked(callOperation).getMockImplementation()!
      vi.mocked(callOperation).mockImplementation(async (name, args) => {
        if (name === 'financeWorkspace.status' && failed)
          return {
            data: {
              project_id: args.project_id,
              readiness: 'select_workspace',
              access: [
                {
                  ...access,
                  readiness,
                  canonical_root: '/private/customer',
                  diagnostic: 'private-token',
                },
                { ...access, workspace_ref: 'finance:other', readiness: 'ready' },
              ],
            },
          }
        if (name === 'finance.report.render')
          return {
            state: 'current',
            snapshot,
            content_base64: btoa('Prior private report'),
            content_type: 'text/markdown',
            filename: 'prior.md',
          }
        return basics(name, args)
      })
      const { wrapper } = await mounted()
      expect(wrapper.text()).toContain('USD 90,071,992,547,409.93')
      await wrapper
        .findAll('button')
        .find((button) => button.text() === 'Reports')!
        .trigger('click')
      await wrapper
        .findAll('button')
        .find((button) => button.text() === 'View period report')!
        .trigger('click')
      await flushPromises()
      expect(wrapper.text()).toContain('Prior private report')
      expect(wrapper.find('a[download="prior.md"]').exists()).toBe(true)
      const priorQueries = vi
        .mocked(callOperation)
        .mock.calls.filter(([name]) => name === 'finance.query').length
      failed = true
      await wrapper.get('[data-test="refresh-finance"]').trigger('click')
      await flushPromises()
      expect(wrapper.get('[role="alert"]').text()).toContain(expected)
      await wrapper.get('[data-test="refresh-finance"]').trigger('click')
      await flushPromises()
      expect(wrapper.get('[role="alert"]').text()).toContain(expected)
      expect(wrapper.text()).not.toContain('Prior private report')
      expect(wrapper.text()).not.toContain('USD 90,071,992,547,409.93')
      expect(wrapper.text()).not.toContain('/private/customer')
      expect(wrapper.text()).not.toContain('private-token')
      expect(wrapper.text()).not.toContain('unknown-private-diagnostic')
      expect(wrapper.find('a[download]').exists()).toBe(false)
      expect(revoke).toHaveBeenCalledWith('blob:prior-finance-report')
      expect(
        vi.mocked(callOperation).mock.calls.filter(([name]) => name === 'finance.query'),
      ).toHaveLength(priorQueries)
      expect(
        vi.mocked(callOperation).mock.calls.some(([name]) => name === 'financeWorkspace.select'),
      ).toBe(false)
      wrapper.unmount()
    },
  )
  it('explains absent workspace configuration without making a write', async () => {
    vi.mocked(callOperation).mockResolvedValue({
      data: { project_id: 1, readiness: 'not_configured', access: [] },
    })
    const { wrapper } = await mounted()
    expect(wrapper.get('[role="alert"]').text()).toContain('No finance workspace is configured.')
    expect(vi.mocked(callOperation).mock.calls.map(([name]) => name)).toEqual([
      'financeWorkspace.status',
    ])
    wrapper.unmount()
  })
  it('keeps the newer selected request after queue reconciliation and a late original acknowledgement', async () => {
    const queue = new Map<number, Record<string, unknown>>()
    const attempts: Record<string, unknown>[] = []
    let finishOriginal!: () => void
    const basics = vi.mocked(callOperation).getMockImplementation()!
    vi.mocked(callOperation).mockImplementation(async (name, args) => {
      if (name === 'agentRequest.list') return { items: [...queue.values()] }
      if (name === 'agentRequest.get')
        return { ...queue.get(Number(args.request_id)), status: 'run-created', run_plan_id: 3 }
      if (name === 'finance.report.render') return { state: 'run-created' }
      if (name !== 'localAgentChat.createMessage') return basics(name, args)
      attempts.push(JSON.parse(JSON.stringify(args)))
      const request = {
        id: queue.size + 1,
        request_key: `local-agent-chat:${args.thread_key}:${args.message_key}`,
        status: 'new',
        metadata_json: { metadata_json: args.metadata_json },
      }
      queue.set(request.id, request)
      const response = { data: { agent_request: request } }
      if (request.id === 1)
        return new Promise((resolve) => {
          finishOriginal = () => resolve(response)
        })
      return response
    })
    const { wrapper, router } = await mounted()
    await wrapper
      .findAll('button')
      .find((b) => b.text() === 'Request agent analysis')!
      .trigger('click')
    await flushPromises()
    await wrapper.get('[data-test="refresh-finance"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Original analysis request 1 verified in Agent requests')
    expect(wrapper.text()).toContain('Run prepared — awaiting execution')
    expect(wrapper.find('a[href="/projects/1/agent-requests?request=1"]').exists()).toBe(true)
    await wrapper
      .findAll('button')
      .find((b) => b.text() === 'Request new agent analysis')!
      .trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.analysis).toBe('2')
    expect(wrapper.find('a[href="/projects/1/agent-requests?request=2"]').exists()).toBe(true)
    finishOriginal()
    await flushPromises()
    expect(router.currentRoute.value.query.analysis).toBe('2')
    expect(wrapper.find('a[href="/projects/1/agent-requests?request=2"]').exists()).toBe(true)
    expect(wrapper.find('a[href="/projects/1/agent-requests?request=1"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('Awaiting agent — a host must claim this request')
    expect(attempts).toHaveLength(2)
    expect(attempts[1]!.message_key).not.toBe(attempts[0]!.message_key)
    expect(queue.size).toBe(2)
    wrapper.unmount()
  })
  it('retries a persisted request after a lost response with the exact same intent and payload', async () => {
    const queue = new Map<string, Record<string, unknown>>()
    const attempts: Record<string, unknown>[] = []
    const basics = vi.mocked(callOperation).getMockImplementation()!
    vi.mocked(callOperation).mockImplementation(async (name, args) => {
      if (name !== 'localAgentChat.createMessage') return basics(name, args)
      const payload = JSON.parse(JSON.stringify(args)) as Record<string, unknown>
      attempts.push(payload)
      const key = String(args.message_key)
      if (!queue.has(key))
        queue.set(key, {
          id: queue.size + 1,
          status: 'new',
          metadata_json: { metadata_json: args.metadata_json },
        })
      if (attempts.length === 1) throw new Error('Lost response after persisted queue request')
      return { data: { agent_request: queue.get(key) } }
    })
    const { wrapper } = await mounted()
    const submit = () =>
      wrapper
        .findAll('button')
        .find((b) =>
          /^(Request (new )?agent analysis|Retry original analysis request)$/.test(b.text()),
        )!
        .trigger('click')
    await submit()
    await flushPromises()
    expect(wrapper.text()).not.toContain('Reviewed analysis · current source')
    await submit()
    await flushPromises()
    expect(attempts).toHaveLength(2)
    expect(attempts[1]).toEqual(attempts[0])
    expect(queue.size).toBe(1)
    expect(wrapper.text()).toContain('Request 1 ·')
    expect(wrapper.text()).toContain('Awaiting agent — a host must claim this request')
    await submit()
    await flushPromises()
    expect(attempts[2]!.message_key).not.toBe(attempts[0]!.message_key)
    expect(queue.size).toBe(2)
    wrapper.unmount()
  })
  it.each(['filters', 'source'])(
    'retains the original safe request when %s changes before retry',
    async (change) => {
      const attempts: Record<string, unknown>[] = []
      let currentSnapshot = snapshot
      const basics = vi.mocked(callOperation).getMockImplementation()!
      vi.mocked(callOperation).mockImplementation(async (name, args) => {
        if (name === 'finance.query')
          return { data: { ...projection(args), snapshot: currentSnapshot } }
        if (name !== 'localAgentChat.createMessage') return basics(name, args)
        attempts.push(JSON.parse(JSON.stringify(args)))
        if (attempts.length === 1) throw new Error('Lost response after persisted queue request')
        return {
          data: {
            agent_request: {
              id: 7,
              status: 'new',
              metadata_json: { metadata_json: args.metadata_json },
            },
          },
        }
      })
      const { wrapper } = await mounted()
      const submit = () =>
        wrapper
          .findAll('button')
          .find((b) =>
            /^(Request (new )?agent analysis|Retry original analysis request)$/.test(b.text()),
          )!
          .trigger('click')
      await submit()
      await flushPromises()
      if (change === 'filters') {
        await wrapper.get('[data-test="finance-search"]').setValue('private-current-search')
        await wrapper.get('[data-test="apply-finance-filters"]').trigger('click')
        await flushPromises()
        await wrapper
          .findAll('[role="combobox"]')
          .find((c) => c.text().includes('Supported opportunities'))!
          .trigger('click')
        await wrapper
          .findAll('[role="option"]')
          .find((o) => o.text() === 'Spending review')!
          .trigger('click')
      } else {
        currentSnapshot = { ...snapshot, revision: 2, sha256: 'b'.repeat(64) }
        await wrapper.get('[data-test="refresh-finance"]').trigger('click')
      }
      await flushPromises()
      await submit()
      await flushPromises()
      expect(attempts).toHaveLength(2)
      expect(attempts[1]).toEqual(attempts[0])
      expect(wrapper.text()).toContain('Request 7 ·')
      expect(wrapper.text()).not.toContain('Reviewed analysis · current source')
      if (change === 'source')
        expect(wrapper.text()).toContain('Analysis request is historical — source changed')
      wrapper.unmount()
    },
  )
  it.each(['before', 'during'])(
    'retains an uncertain submission lost %s project route remounts without sending into another project',
    async (timing) => {
      const attempts: Record<string, unknown>[] = []
      let loseResponse!: () => void
      const basics = vi.mocked(callOperation).getMockImplementation()!
      vi.mocked(callOperation).mockImplementation(async (name, args) => {
        if (name !== 'localAgentChat.createMessage') return basics(name, args)
        attempts.push(JSON.parse(JSON.stringify(args)))
        if (attempts.length === 1) {
          if (timing === 'during')
            return new Promise((_resolve, reject) => {
              loseResponse = () => reject(new Error('Lost response after persisted queue request'))
            })
          throw new Error('Lost response after persisted queue request')
        }
        return {
          data: {
            agent_request: {
              id: 7,
              status: 'new',
              metadata_json: { metadata_json: args.metadata_json },
            },
          },
        }
      })
      const { wrapper, router } = await mounted()
      const submit = () =>
        wrapper
          .findAll('button')
          .find((b) =>
            /^(Request (new )?agent analysis|Retry original analysis request)$/.test(b.text()),
          )!
          .trigger('click')
      await submit()
      await flushPromises()
      await router.push('/projects/2/finance')
      await flushPromises()
      if (timing === 'during') {
        loseResponse()
        await flushPromises()
      }
      expect(attempts).toHaveLength(1)
      expect(wrapper.text()).not.toContain('Analysis request submission is unverified')
      await router.push('/projects/1/finance')
      await flushPromises()
      await submit()
      await flushPromises()
      expect(attempts).toHaveLength(2)
      expect(attempts[1]).toEqual(attempts[0])
      expect(wrapper.text()).toContain('Request 7 ·')
      wrapper.unmount()
    },
  )
  it('pauses new submissions in another workspace and retries only the original authorization', async () => {
    const attempts: Record<string, unknown>[] = []
    const basics = vi.mocked(callOperation).getMockImplementation()!
    vi.mocked(callOperation).mockImplementation(async (name, args) => {
      if (name === 'financeWorkspace.status')
        return {
          data: {
            readiness: 'ready',
            access: [access, { ...access, workspace_ref: 'finance:two', access_id: 'access:two' }],
          },
        }
      if (name !== 'localAgentChat.createMessage') return basics(name, args)
      attempts.push(JSON.parse(JSON.stringify(args)))
      if (attempts.length === 1) throw new Error('Lost response after persisted queue request')
      return {
        data: {
          agent_request: {
            id: 7,
            status: 'new',
            metadata_json: { metadata_json: args.metadata_json },
          },
        },
      }
    })
    const { wrapper } = await mounted()
    await wrapper
      .findAll('button')
      .find((b) => b.text() === 'Request agent analysis')!
      .trigger('click')
    await flushPromises()
    async function selectWorkspace(label: string) {
      await wrapper
        .findAll('[role="combobox"]')
        .find((c) => c.text().includes('finance:'))!
        .trigger('click')
      await wrapper
        .findAll('[role="option"]')
        .find((o) => o.text() === label)!
        .trigger('click')
      await flushPromises()
    }
    await selectWorkspace('finance:two')
    for (const label of ['Request agent analysis', 'Retry original analysis request']) {
      const button = wrapper.findAll('button').find((b) => b.text() === label)!
      expect(button.attributes('disabled')).toBeDefined()
      await button.trigger('click')
    }
    expect(attempts).toHaveLength(1)
    expect(wrapper.text()).toContain('Return to the original workspace authorization')
    await selectWorkspace('finance:one')
    await wrapper
      .findAll('button')
      .find((b) => b.text() === 'Retry original analysis request')!
      .trigger('click')
    await flushPromises()
    expect(attempts[1]).toEqual(attempts[0])
    wrapper.unmount()
  })
  it.each(['exact', 'changed'])(
    'reconciles only an %s persisted intent and never treats a receipt as reviewed completion',
    async (metadataMatch) => {
      const attempts: Record<string, unknown>[] = []
      let queued: Record<string, unknown> | null = null
      const basics = vi.mocked(callOperation).getMockImplementation()!
      vi.mocked(callOperation).mockImplementation(async (name, args) => {
        if (name === 'localAgentChat.createMessage') {
          attempts.push(JSON.parse(JSON.stringify(args)))
          queued = {
            id: 7,
            request_key: `local-agent-chat:${args.thread_key}:${args.message_key}`,
            status: 'resolved',
            metadata_json: {
              metadata_json: {
                ...(args.metadata_json as Record<string, unknown>),
                ...(metadataMatch === 'changed' && attempts.length === 1
                  ? { analysis_kind: 'spending' }
                  : {}),
              },
            },
          }
          if (attempts.length === 1) throw new Error('Lost response after persisted queue request')
          return { data: { agent_request: queued } }
        }
        if (name === 'agentRequest.list') return { items: queued ? [queued] : [] }
        if (name === 'agentRequest.get') return queued
        if (name === 'finance.report.render') return { state: 'result_unavailable' }
        return basics(name, args)
      })
      const { wrapper } = await mounted()
      await wrapper
        .findAll('button')
        .find((b) => b.text() === 'Request agent analysis')!
        .trigger('click')
      await flushPromises()
      await wrapper.get('[data-test="refresh-finance"]').trigger('click')
      await flushPromises()
      const retry = wrapper
        .findAll('button')
        .find((b) => b.text() === 'Retry original analysis request')
      if (metadataMatch === 'exact') {
        expect(retry).toBeUndefined()
        expect(wrapper.text()).toContain('Original analysis request 7 verified in Agent requests')
        expect(wrapper.text()).toContain('Reviewed result unavailable')
        expect(attempts).toHaveLength(1)
      } else {
        expect(retry).toBeDefined()
        expect(wrapper.text()).not.toContain('Original analysis request 7 verified')
        await retry!.trigger('click')
        await flushPromises()
        expect(attempts[1]).toEqual(attempts[0])
      }
      expect(wrapper.text()).not.toContain('Reviewed analysis · current source')
      wrapper.unmount()
    },
  )
  it('reconciles a retained submission after authorization changes without replaying a stale generation', async () => {
    let generation = 1
    let exposeReceipt = false
    let queued: Record<string, unknown> | null = null
    const basics = vi.mocked(callOperation).getMockImplementation()!
    vi.mocked(callOperation).mockImplementation(async (name, args) => {
      if (name === 'financeWorkspace.status')
        return { data: { readiness: 'ready', access: [{ ...access, generation }] } }
      if (name === 'localAgentChat.createMessage') {
        queued = {
          id: 7,
          request_key: `local-agent-chat:${args.thread_key}:${args.message_key}`,
          status: 'new',
          metadata_json: { metadata_json: args.metadata_json },
        }
        throw new Error('Lost response after persisted queue request')
      }
      if (name === 'agentRequest.list') return { items: exposeReceipt && queued ? [queued] : [] }
      if (name === 'agentRequest.get') return queued
      if (name === 'finance.report.render') return { state: 'historical_access' }
      return basics(name, args)
    })
    const { wrapper } = await mounted()
    await wrapper
      .findAll('button')
      .find((b) => b.text() === 'Request agent analysis')!
      .trigger('click')
    await flushPromises()
    generation = 2
    await wrapper.get('[data-test="refresh-finance"]').trigger('click')
    await flushPromises()
    for (const label of ['Request agent analysis', 'Retry original analysis request']) {
      const button = wrapper.findAll('button').find((b) => b.text() === label)!
      expect(button.attributes('disabled')).toBeDefined()
      await button.trigger('click')
    }
    exposeReceipt = true
    await wrapper.get('[data-test="refresh-finance"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Original analysis request 7 verified in Agent requests')
    expect(wrapper.text()).toContain('Historical workspace authorization')
    expect(wrapper.text()).not.toContain('Reviewed analysis · current source')
    expect(
      vi
        .mocked(callOperation)
        .mock.calls.filter((call) => call[0] === 'localAgentChat.createMessage'),
    ).toHaveLength(1)
    wrapper.unmount()
  })
  it.each(['lost', 'saved'])(
    'handles a %s submission response during source refresh without replacing its intent or leaving loading stuck',
    async (outcome) => {
      const attempts: Record<string, unknown>[] = []
      let currentSnapshot = snapshot
      let finish!: () => void
      const basics = vi.mocked(callOperation).getMockImplementation()!
      vi.mocked(callOperation).mockImplementation(async (name, args) => {
        if (name === 'finance.query')
          return { data: { ...projection(args), snapshot: currentSnapshot } }
        if (name !== 'localAgentChat.createMessage') return basics(name, args)
        attempts.push(JSON.parse(JSON.stringify(args)))
        const response = {
          data: {
            agent_request: {
              id: 7,
              status: 'new',
              metadata_json: { metadata_json: args.metadata_json },
            },
          },
        }
        if (attempts.length === 1)
          return new Promise((resolve, reject) => {
            finish = () =>
              outcome === 'lost'
                ? reject(new Error('Lost response after persisted queue request'))
                : resolve(response)
          })
        return response
      })
      const { wrapper } = await mounted()
      await wrapper
        .findAll('button')
        .find((b) => b.text() === 'Request agent analysis')!
        .trigger('click')
      await flushPromises()
      currentSnapshot = { ...snapshot, revision: 2, sha256: 'b'.repeat(64) }
      await wrapper.get('[data-test="refresh-finance"]').trigger('click')
      await flushPromises()
      expect(
        wrapper
          .findAll('button')
          .find((b) => b.text() === 'Retry original analysis request')!
          .attributes('disabled'),
      ).toBeDefined()
      expect(attempts).toHaveLength(1)
      finish()
      await flushPromises()
      if (outcome === 'lost') {
        await wrapper
          .findAll('button')
          .find((b) => b.text() === 'Retry original analysis request')!
          .trigger('click')
        await flushPromises()
        expect(attempts[1]).toEqual(attempts[0])
      } else expect(attempts).toHaveLength(1)
      expect(wrapper.text()).toContain('Analysis request is historical — source changed')
      expect(wrapper.text()).not.toContain('Reviewed analysis · current source')
      expect(
        wrapper
          .findAll('button')
          .find((b) => b.text() === 'Request new agent analysis')!
          .attributes('disabled'),
      ).toBeUndefined()
      wrapper.unmount()
    },
  )
  it.each([
    ['run-created', 'Run prepared — awaiting execution'],
    ['run-started', 'Running in the linked agent run'],
    ['run-blocked', 'Agent run blocked — inspect the linked run'],
    ['failed', 'Analysis failed'],
    ['run-aborted', 'Agent run aborted — reviewed result not published'],
    ['completed_unpublished', 'Run completed — reviewed result not published'],
    ['run_unavailable', 'Linked run unavailable — inspect the agent request'],
    ['historical_source', 'Analysis request is historical — source changed'],
    ['historical_access', 'Historical workspace authorization'],
    ['source_unavailable', 'Request source unavailable — request new analysis'],
    ['current', 'Reviewed analysis · current source'],
  ])(
    'renders canonical analysis state %s without adopting or changing scope',
    async (state, label) => {
      const basics = vi.mocked(callOperation).getMockImplementation()!
      const metadata = {
        workspace_ref: 'finance:one',
        source_snapshot: snapshot,
        query: { start: '2026-09-01', end: '2026-09-30' },
      }
      vi.mocked(callOperation).mockImplementation(async (name, args) => {
        if (name === 'agentRequest.list')
          return {
            items: [
              {
                id: 7,
                status: 'new',
                run_plan_id: 3,
                metadata_json: { metadata_json: metadata },
              },
            ],
          }
        if (name === 'agentRequest.get')
          return {
            id: 7,
            status: state === 'current' ? 'resolved' : 'run-created',
            run_plan_id: 3,
            metadata_json: { metadata_json: metadata },
          }
        if (name === 'finance.report.render')
          return { state, request_id: 7, next_action: 'inspect_agent_request' }
        return basics(name, args)
      })
      const { wrapper } = await mounted('?analysis=7')
      await wrapper
        .findAll('button')
        .find((b) => b.text() === 'Reports')!
        .trigger('click')
      await wrapper
        .findAll('button')
        .find((b) => b.text() === 'Refresh analysis status')!
        .trigger('click')
      await flushPromises()
      expect(wrapper.text()).toContain(label)
      expect(wrapper.text()).toContain(
        `Request 7 · ${state === 'current' ? 'resolved' : 'run-created'}`,
      )
      expect(wrapper.text()).not.toContain('Request 7 · new')
      if (state !== 'run-started')
        expect(wrapper.text()).not.toContain('Running in the linked agent run')
      expect(wrapper.text()).toContain('2026-09-01')
      expect(wrapper.text()).toContain('2026-09-30')
      const calls = vi.mocked(callOperation).mock.calls
      expect(
        calls.some((c) => c[0] === 'finance.record.commit' || c[0].startsWith('runPlan.')),
      ).toBe(false)
      expect(calls.find((c) => c[0] === 'finance.report.render')![1]).toMatchObject({
        start: '2026-09-01',
        end: '2026-09-30',
        analysis_request_id: 7,
      })
      wrapper.unmount()
    },
  )
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
      const remaining = { ...selected }
      delete remaining.q
      delete remaining.project_id
      delete remaining.response_mode
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
