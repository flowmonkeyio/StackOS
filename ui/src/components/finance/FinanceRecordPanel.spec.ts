import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import FinanceRecordPanel from './FinanceRecordPanel.vue'
import { callOperation } from '@/lib/operations'
import { ApiError } from '@/lib/client'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))
const snapshot = {
  revision: 1,
  sha256: 'a'.repeat(64),
  schema_version: 'local-json-v1',
  read_at: '2026-10-13',
  custody_issues: [],
}
const detail = {
  requested_ref: 'receipt:legacy',
  current_ref: 'receipt:legacy',
  collection: 'receipts',
  record: {
    business_date: '0001-01-01',
    merchant: 'Synthetic vendor',
    amount: { amount_minor: '9007199254740993', currency: 'USD' },
    business_purpose: 'Supplied purpose',
  },
  original: { business_date: '0001-01-01' },
  lineage: [],
  source_refs: ['source:synthetic'],
  evidence: [{ attachment_ref: 'attachment:one', state: 'available' }],
  editable_fields: ['date', 'amount', 'merchant', 'business_purpose'],
  provider_fields_editable: false,
}
const props = {
  projectId: 1,
  access: {
    project_id: 1,
    workspace_ref: 'finance:one',
    generation: 1,
    modes: ['read', 'correct', 'export'],
    access_id: 'access:one',
    backend_key: 'local-json' as const,
    revoked_at: null,
  },
  snapshot,
  recordRef: 'receipt:legacy',
  modelValue: true,
}
afterEach(() => {
  vi.resetAllMocks()
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

describe('reviewed save and private originals', () => {
  it.each(['success', 'stale', 'uncertain'])(
    'preserves exact preview and scope through %s save',
    async (outcome) => {
      let attempts = 0
      vi.mocked(callOperation).mockImplementation(async (name) => {
        if (name === 'finance.record.get') return { data: detail }
        if (name === 'finance.record.preview')
          return {
            data: {
              preview_digest: 'b'.repeat(64),
              source_snapshot: snapshot,
              changed_refs: ['receipt:replacement'],
              diff: { fields: { date: '2026-09-01' } },
              state: 'valid',
              financial_decision_adopted: false,
            },
          }
        attempts++
        if (outcome === 'stale')
          throw new ApiError('Source changed', 409, {
            detail: 'Source changed',
            data: { finance_code: 'stale_snapshot' },
          })
        if (outcome === 'uncertain' && attempts === 1)
          throw new Error('Synthetic interrupted response')
        return {
          data: {
            request_id: 'synthetic',
            changed_refs: ['receipt:replacement'],
            snapshot: { ...snapshot, revision: 2 },
            reread_result: 'verified',
            idempotency_replay: attempts > 1,
          },
        }
      })
      const panel = mount(FinanceRecordPanel, {
        props,
        attachTo: document.body,
        global: { stubs: { teleport: true } },
      })
      await flushPromises()
      await panel.get('[data-test="edit-record"]').trigger('click')
      await panel.get('[data-test="date"]').setValue('2026-09-01')
      await panel.get('[data-test="reason"]').setValue('Sourced synthetic correction')
      await panel.get('form').trigger('submit')
      await flushPromises()
      await panel.get('[data-test="save-edit"]').trigger('click')
      await flushPromises()
      const saved = vi
        .mocked(callOperation)
        .mock.calls.find((c) => c[0] === 'finance.record.commit')![1]
      expect(saved).toMatchObject({
        project_id: 1,
        workspace_ref: 'finance:one',
        access_generation: 1,
        expected_snapshot: { revision: 1, sha256: snapshot.sha256 },
        record_ref: 'receipt:legacy',
        action: 'correct-receipt-facts',
        fields: { date: '2026-09-01' },
        source_refs: ['source:synthetic'],
        preview_digest: 'b'.repeat(64),
      })
      expect(saved.fields).not.toHaveProperty('provider_status')
      if (outcome === 'success') expect(panel.emitted('saved')).toHaveLength(1)
      else if (outcome === 'stale') {
        expect(panel.text()).toContain('source changed')
        expect(panel.get('[data-test="save-edit"]').attributes('disabled')).toBeDefined()
        expect(panel.emitted('saved')).toBeUndefined()
      } else {
        expect(panel.text()).toContain('uncertain')
        expect(panel.get('[data-test="cancel-edit"]').attributes('disabled')).toBeDefined()
        await panel.get('[data-test="save-edit"]').trigger('click')
        await flushPromises()
        expect(
          vi
            .mocked(callOperation)
            .mock.calls.filter((c) => c[0] === 'finance.record.commit')
            .at(-1)![1],
        ).toEqual(saved)
      }
      panel.unmount()
    },
  )
  it('keeps provider records protected even if a field is presented as editable', async () => {
    vi.mocked(callOperation).mockResolvedValue({
      data: { ...detail, collection: 'billing_versions' },
    })
    const panel = mount(FinanceRecordPanel, { props, global: { stubs: { teleport: true } } })
    await flushPromises()
    expect(panel.find('[data-test="edit-record"]').exists()).toBe(false)
    expect(panel.text()).toContain('read-only')
    panel.unmount()
  })
  it('reads bare raw original content and revokes the private Blob on close', async () => {
    const create = vi.fn(() => 'blob:synthetic-original'),
      revoke = vi.fn()
    const OriginalURL = URL
    vi.stubGlobal(
      'URL',
      class extends OriginalURL {
        static override createObjectURL = create
        static override revokeObjectURL = revoke
      },
    )
    vi.mocked(callOperation).mockImplementation(async (name) =>
      name === 'finance.record.get'
        ? { data: detail }
        : {
            state: 'available',
            content_base64: btoa('synthetic original'),
            content_type: 'application/pdf',
            inline_allowed: true,
            filename: 'original.pdf',
            sha256: 'c'.repeat(64),
          },
    )
    const panel = mount(FinanceRecordPanel, { props, global: { stubs: { teleport: true } } })
    await flushPromises()
    await panel
      .findAll('button')
      .find((button) => button.text().includes('View attachment:one'))!
      .trigger('click')
    await flushPromises()
    expect(panel.get('a[download]').attributes('href')).toBe('blob:synthetic-original')
    expect(vi.mocked(callOperation).mock.calls.at(-1)![1]).toMatchObject({
      response_mode: 'raw',
      attachment_ref: 'attachment:one',
      expected_snapshot: { revision: 1, sha256: snapshot.sha256 },
    })
    await panel.setProps({ modelValue: false })
    expect(revoke).toHaveBeenCalledWith('blob:synthetic-original')
    panel.unmount()
  })
})
describe('sourced finance editor', () => {
  it('shows source evidence before edit and cancel never previews or commits', async () => {
    vi.mocked(callOperation).mockResolvedValue({ data: detail })
    const panel = mount(FinanceRecordPanel, {
      props,
      attachTo: document.body,
      global: { stubs: { teleport: true } },
    })
    await flushPromises()
    expect(panel.text()).toContain('0001-01-01')
    expect(panel.text()).toContain('Original evidence')
    await panel.get('[data-test="edit-record"]').trigger('click')
    await panel.get('[data-test="date"]').setValue('2026-09-01')
    await panel.get('[data-test="cancel-edit"]').trigger('click')
    expect(vi.mocked(callOperation).mock.calls.map((c) => c[0])).toEqual(['finance.record.get'])
    expect(panel.text()).toContain('0001-01-01')
    panel.unmount()
  })
  it('does not let a cancelled slow preview re-enable save', async () => {
    let resolve!: (v: unknown) => void
    vi.mocked(callOperation).mockImplementation(async (name) =>
      name === 'finance.record.get'
        ? { data: detail }
        : new Promise((r) => {
            resolve = r
          }),
    )
    const panel = mount(FinanceRecordPanel, {
      props,
      attachTo: document.body,
      global: { stubs: { teleport: true } },
    })
    await flushPromises()
    await panel.get('[data-test="edit-record"]').trigger('click')
    await panel.get('[data-test="reason"]').setValue('Verify synthetic source date')
    await panel.get('[data-test="date"]').setValue('2026-09-01')
    await panel.get('[data-test="preview-edit"]').trigger('click')
    await panel.get('[data-test="cancel-edit"]').trigger('click')
    resolve({
      data: {
        preview_digest: 'b'.repeat(64),
        source_snapshot: snapshot,
        diff: {},
        changed_refs: [],
        state: 'valid',
        financial_decision_adopted: false,
      },
    })
    await flushPromises()
    expect(panel.find('[data-test="save-edit"]').exists()).toBe(false)
    expect(vi.mocked(callOperation).mock.calls.some((c) => c[0] === 'finance.record.commit')).toBe(
      false,
    )
    panel.unmount()
  })
})
