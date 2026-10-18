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
    workspace_ref: 'finance:project:1',
    generation: 1,
    modes: ['read', 'correct', 'export'],
    access_id: 'access:one',
    backend_key: 'local-json' as const,
    readiness: 'ready' as const,
    snapshot,
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
        workspace_ref: 'finance:project:1',
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
      .find((button) => button.attributes('data-document-ref') === 'attachment:one')!
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

describe('invoice and receipt originals', () => {
  const summary = (state = 'available', available = 1, unavailable = 0, unverified = 0) => ({
    state,
    document_count: available + unavailable + unverified,
    available_count: available,
    unavailable_count: unavailable,
    not_verified_count: unverified,
  })
  const invoice = {
    attachment_ref: 'attachment:invoice',
    document_type: 'invoice',
    filename: 'Supplier invoice.pdf',
    availability: 'available',
    retention_state: 'retained',
    state: 'retained',
    reason: null,
    media_type: 'application/pdf',
    bytes: 32,
    sha256: 'c'.repeat(64),
    source_refs: ['source:old'],
    source_context: [
      {
        requested_ref: 'source:old',
        current_ref: 'source:current',
        source_ref: 'source:old',
        historical: true,
      },
    ],
    receipt_context: [
      {
        requested_ref: 'receipt:old',
        current_ref: 'receipt:current',
        receipt_ref: 'receipt:current',
        historical: false,
      },
    ],
  }
  const receipt = {
    ...invoice,
    attachment_ref: 'attachment:receipt',
    document_type: 'receipt',
    filename: 'Payment receipt.pdf',
    source_refs: ['source:payment'],
    source_context: [],
    receipt_context: [],
  }
  const documentDetail = {
    ...detail,
    evidence: [invoice, receipt],
    document_summary: { invoice: summary(), receipt: summary() },
  }
  const content = (filename = 'stored-original.pdf') => ({
    state: 'available',
    content_base64: btoa('exact synthetic original bytes'),
    content_type: 'application/pdf',
    inline_allowed: true,
    filename,
    sha256: 'c'.repeat(64),
  })
  const mountPanel = () =>
    mount(FinanceRecordPanel, {
      props,
      attachTo: document.body,
      global: { stubs: { teleport: true } },
    })
  function blobSpies() {
    const create = vi.fn(() => 'blob:document'),
      revoke = vi.fn(),
      OriginalURL = URL
    vi.stubGlobal(
      'URL',
      class extends OriginalURL {
        static override createObjectURL = create
        static override revokeObjectURL = revoke
      },
    )
    return { create, revoke }
  }
  it('identifies both kinds by original filename and reads each exact scoped ref', async () => {
    const { create, revoke } = blobSpies()
    vi.mocked(callOperation).mockImplementation(async (name) =>
      name === 'finance.record.get' ? { data: documentDetail } : content(),
    )
    const panel = mountPanel()
    await flushPromises()
    expect(panel.get('section[aria-label="Invoice"]').text()).toContain('Invoice · Available · 1')
    expect(panel.get('section[aria-label="Receipt"]').text()).toContain('Receipt · Available · 1')
    expect(panel.text()).toContain('1 linked · 1 available · 0 unavailable · 0 not verified')
    expect(panel.text()).toContain('32 bytes')
    expect(panel.text()).toContain(
      'Historical source: source:old · requested source:old · current source:current',
    )
    expect(panel.text()).toContain('Current receipt record: receipt:current')
    for (const document of [invoice, receipt]) {
      const button = panel.get(`[data-document-ref="${document.attachment_ref}"]`)
      expect(button.attributes('aria-label')).toBe(`Open ${document.filename}`)
      expect(button.element.tagName).toBe('BUTTON')
      expect(button.classes()).toContain('focus-ring')
      ;(button.element as HTMLButtonElement).focus()
      expect(window.document.activeElement).toBe(button.element)
      await button.trigger('click')
      await flushPromises()
      expect(vi.mocked(callOperation).mock.calls.at(-1)).toEqual([
        'finance.evidence.read',
        {
          project_id: 1,
          workspace_ref: 'finance:project:1',
          access_generation: 1,
          attachment_ref: document.attachment_ref,
          expected_snapshot: { revision: 1, sha256: snapshot.sha256 },
          response_mode: 'raw',
        },
      ])
      expect(panel.get('a[download]').text()).toBe(`Download ${document.filename}`)
      expect(panel.get('a[download]').attributes('download')).toBe('stored-original.pdf')
      expect(panel.get('iframe').attributes('title')).toBe(
        `Verified original: ${document.filename}`,
      )
    }
    expect(create).toHaveBeenCalledTimes(2)
    expect(revoke).toHaveBeenCalledTimes(1)
    expect(panel.text()).toContain('does not confirm payment or posted books')
    panel.unmount()
    expect(revoke).toHaveBeenCalledTimes(2)
  })
  it.each(['unavailable', 'stale', 'too_large', 'exception'])(
    'clears the previous original and names the failed %s read separately from snapshot custody',
    async (outcome) => {
      const { revoke } = blobSpies()
      vi.mocked(callOperation).mockImplementation(async (name, args) => {
        if (name === 'finance.record.get') return { data: documentDetail }
        if (args.attachment_ref === invoice.attachment_ref) return content()
        if (outcome === 'exception') throw new Error('synthetic denied read')
        return { state: outcome, filename: 'storage-name.pdf' }
      })
      const panel = mountPanel()
      await flushPromises()
      await panel.get('[data-document-ref="attachment:invoice"]').trigger('click')
      await flushPromises()
      expect(panel.find('iframe').exists()).toBe(true)
      await panel.get('[data-document-ref="attachment:receipt"]').trigger('click')
      await flushPromises()
      expect(revoke).toHaveBeenCalledWith('blob:document')
      expect(panel.find('iframe').exists()).toBe(false)
      expect(panel.find('a[download]').exists()).toBe(false)
      expect(panel.get('section[aria-label="Receipt"]').text()).toContain(
        'Read outcome: cannot open this original',
      )
      expect(panel.get('section[aria-label="Receipt"]').text()).toContain(
        'Available · verified in this snapshot',
      )
      expect(panel.text()).toContain('Payment receipt.pdf cannot')
      expect(panel.text()).not.toContain('storage-name.pdf')
      expect(panel.text()).not.toContain('Missing')
      if (outcome === 'too_large') expect(panel.text()).toContain('exceeds the reader limit')
      const refresh = panel
        .findAll('button')
        .find((button) => button.text() === 'Refresh original availability')!
      await refresh.trigger('click')
      expect(panel.emitted('refresh')).toHaveLength(1)
      expect(vi.mocked(callOperation).mock.calls.map(([name]) => name)).toEqual([
        'finance.record.get',
        'finance.evidence.read',
        'finance.evidence.read',
      ])
      panel.unmount()
    },
  )
  it('separates mixed custody and unknown files, disables unavailable originals and escapes filenames', async () => {
    const unsafe = '<img src=x onerror=alert(1)>.pdf'
    const missing = {
      ...invoice,
      attachment_ref: 'attachment:missing',
      filename: 'Unavailable original.pdf',
      availability: 'unavailable',
      reason: 'unavailable',
    }
    vi.mocked(callOperation).mockResolvedValue({
      data: {
        ...documentDetail,
        evidence: [
          invoice,
          missing,
          {
            ...receipt,
            availability: 'unavailable',
            reason: 'quarantined',
            retention_state: 'quarantined',
          },
          { attachment_ref: 'attachment:unknown', filename: unsafe, state: 'retained' },
        ],
        document_summary: {
          invoice: summary('partial', 1, 1),
          receipt: summary('unavailable', 0, 1),
        },
      },
    })
    const panel = mountPanel()
    await flushPromises()
    expect(panel.text()).toContain('Invoice · Partial · 1/2 available')
    expect(panel.text()).toContain('2 linked · 1 available · 1 unavailable · 0 not verified')
    expect(panel.get('section[aria-label="Unidentified documents"]').text()).toContain(
      '1 linked original(s)',
    )
    expect(
      panel.get('[data-document-ref="attachment:missing"]').attributes('disabled'),
    ).toBeDefined()
    expect(
      panel.get('[data-document-ref="attachment:receipt"]').attributes('disabled'),
    ).toBeDefined()
    expect(panel.text()).toContain('original is quarantined')
    const legacy = panel.get('[data-document-ref="attachment:unknown"]')
    expect(legacy.attributes('disabled')).toBeUndefined()
    expect(legacy.attributes('aria-label')).toBe(`Verify and open ${unsafe}`)
    expect(panel.text()).toContain(unsafe)
    expect(panel.find('img').exists()).toBe(false)
    expect(panel.text()).toContain('ask your finance agent')
    panel.unmount()
  })
  it.each(['not_linked', 'unidentified', 'legacy'])(
    'renders %s without claiming a missing or available file',
    async (state) => {
      vi.mocked(callOperation).mockResolvedValue({
        data: {
          ...detail,
          evidence:
            state === 'unidentified'
              ? [
                  {
                    attachment_ref: 'attachment:unknown',
                    document_type: 'unidentified',
                    filename: 'Evidence.pdf',
                    availability: 'not_verified',
                  },
                ]
              : [],
          document_summary:
            state === 'legacy' ? null : { invoice: summary(state, 0), receipt: summary(state, 0) },
        },
      })
      const panel = mountPanel()
      await flushPromises()
      const invoiceGroup = panel.get('section[aria-label="Invoice"]').text()
      expect(invoiceGroup).toContain(
        state === 'legacy'
          ? 'Not verified'
          : state === 'not_linked'
            ? 'Not linked'
            : 'Needs identification',
      )
      expect(invoiceGroup).not.toContain('Available')
      expect(invoiceGroup).not.toContain('Missing')
      panel.unmount()
    },
  )
  it('ignores a late original after another document opens', async () => {
    const { create } = blobSpies()
    let finish!: (value: unknown) => void
    vi.mocked(callOperation).mockImplementation(async (name, args) => {
      if (name === 'finance.record.get') return { data: documentDetail }
      return args.attachment_ref === invoice.attachment_ref
        ? new Promise((resolve) => {
            finish = resolve
          })
        : content()
    })
    const panel = mountPanel()
    await flushPromises()
    await panel.get('[data-document-ref="attachment:invoice"]').trigger('click')
    await panel.get('[data-document-ref="attachment:receipt"]').trigger('click')
    await flushPromises()
    finish(content('late-invoice.pdf'))
    await flushPromises()
    expect(create).toHaveBeenCalledTimes(1)
    expect(panel.get('a[download]').text()).toBe('Download Payment receipt.pdf')
    expect(panel.text()).not.toContain('late-invoice.pdf')
    panel.unmount()
  })
  it.each(['project', 'source', 'generation', 'record', 'revision', 'sha', 'close', 'unmount'])(
    'cleans private bytes and rejects a late original on %s change',
    async (change) => {
      const { create, revoke } = blobSpies()
      let finish!: (value: unknown) => void
      vi.mocked(callOperation).mockImplementation(async (name, args) => {
        if (name === 'finance.record.get') return { data: documentDetail }
        return args.attachment_ref === invoice.attachment_ref
          ? content()
          : new Promise((resolve) => {
              finish = resolve
            })
      })
      const panel = mountPanel()
      await flushPromises()
      await panel.get('[data-document-ref="attachment:invoice"]').trigger('click')
      await flushPromises()
      await panel.get('[data-document-ref="attachment:receipt"]').trigger('click')
      expect(revoke).toHaveBeenCalledWith('blob:document')
      if (change === 'unmount') panel.unmount()
      else
        await panel.setProps(
          change === 'project'
            ? { projectId: 2 }
            : change === 'source'
              ? { access: { ...props.access, workspace_ref: 'finance:project:2' } }
              : change === 'generation'
                ? { access: { ...props.access, generation: 2 } }
                : change === 'record'
                  ? { recordRef: 'receipt:two' }
                  : change === 'revision'
                    ? { snapshot: { ...snapshot, revision: 2 } }
                    : change === 'sha'
                      ? { snapshot: { ...snapshot, sha256: 'b'.repeat(64) } }
                      : { modelValue: false },
        )
      finish(content('late-private.pdf'))
      await flushPromises()
      expect(create).toHaveBeenCalledTimes(1)
      if (change !== 'unmount') {
        expect(panel.find('iframe').exists()).toBe(false)
        expect(panel.find('a[download]').exists()).toBe(false)
        expect(panel.text()).not.toContain('late-private.pdf')
        panel.unmount()
      }
    },
  )
})
describe('sourced finance editor', () => {
  it('preserves typed facts and a verified preview across equivalent source props and display updates', async () => {
    vi.mocked(callOperation).mockImplementation(async (name) =>
      name === 'finance.record.get'
        ? { data: detail }
        : {
            data: {
              preview_digest: 'b'.repeat(64),
              source_snapshot: snapshot,
              diff: { fields: { date: '2026-09-01' } },
              changed_refs: [],
              state: 'valid',
              financial_decision_adopted: false,
            },
          },
    )
    const panel = mount(FinanceRecordPanel, {
      props,
      global: { stubs: { teleport: true } },
    })
    await flushPromises()
    await panel.get('[data-test="edit-record"]').trigger('click')
    await panel.get('[data-test="date"]').setValue('2026-09-01')
    await panel.get('[data-test="reason"]').setValue('Keep this sourced draft')
    await panel.get('form').trigger('submit')
    await flushPromises()
    await panel.setProps({
      access: { ...props.access, modes: [...props.access.modes] },
      snapshot: { ...snapshot },
      reporting: {},
      exponent: 2,
    })
    await flushPromises()
    expect(panel.find('[data-test="save-edit"]').exists()).toBe(true)
    expect((panel.get('[data-test="date"]').element as HTMLInputElement).value).toBe('2026-09-01')
    expect((panel.get('[data-test="reason"]').element as HTMLTextAreaElement).value).toBe(
      'Keep this sourced draft',
    )
    expect(vi.mocked(callOperation).mock.calls.map((call) => call[0])).toEqual([
      'finance.record.get',
      'finance.record.preview',
    ])
    panel.unmount()
  })

  it.each(['record', 'snapshot'])(
    'resets stale editing on a changed %s and rejects a late detail response',
    async (change) => {
      let finishOld!: (value: unknown) => void
      vi.mocked(callOperation)
        .mockResolvedValueOnce({ data: detail })
        .mockImplementationOnce(
          () =>
            new Promise((resolve) => {
              finishOld = resolve
            }),
        )
        .mockResolvedValue({
          data: { ...detail, record: { ...detail.record, merchant: 'Current source vendor' } },
        })
      const panel = mount(FinanceRecordPanel, {
        props,
        global: { stubs: { teleport: true } },
      })
      await flushPromises()
      await panel.get('[data-test="edit-record"]').trigger('click')
      await panel.get('[data-test="date"]').setValue('2026-09-01')
      await panel.setProps(
        change === 'record'
          ? { recordRef: 'receipt:second' }
          : { snapshot: { ...snapshot, revision: 2 } },
      )
      await flushPromises()
      expect(panel.find('[data-test="date"]').exists()).toBe(false)
      await panel.setProps({ snapshot: { ...snapshot, revision: 3, sha256: 'c'.repeat(64) } })
      await flushPromises()
      finishOld({
        data: { ...detail, record: { ...detail.record, merchant: 'Stale private vendor' } },
      })
      await flushPromises()
      expect(panel.text()).toContain('Current source vendor')
      expect(panel.text()).not.toContain('Stale private vendor')
      expect(vi.mocked(callOperation).mock.calls.at(-1)![1]).toMatchObject({
        expected_snapshot: { revision: 3, sha256: 'c'.repeat(64) },
      })
      panel.unmount()
    },
  )
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

describe('canonical precision and correction history', () => {
  const knownDetail = {
    ...detail,
    collection: 'bookkeeping',
    current_ref: 'bookkeeping:current',
    amount: { minor: '2000', currency: 'USD', exponent: 2 },
    record: { ...detail.record, amount: { amount_minor: '2000', currency: 'USD' } },
    editable_fields: ['date', 'amount', 'reporting'],
  }
  const previewResult = {
    data: {
      preview_digest: 'b'.repeat(64),
      source_snapshot: snapshot,
      changed_refs: ['bookkeeping:current'],
      diff: { fields: {} },
      state: 'valid',
      financial_decision_adopted: false,
    },
  }
  const mountPanel = (overrides = {}) =>
    mount(FinanceRecordPanel, {
      props: { ...props, ...overrides },
      attachTo: document.body,
      global: { stubs: { teleport: true } },
    })

  it.each([
    ['[data-test="date"]', '2026-08-01', '2026-07-01'],
    ['[data-test="amount"]', '21.00', '22.00'],
    ['input[maxlength="1"]', '3', '4'],
    ['[data-test="reason"]', 'Changed source reason', 'Latest source reason'],
  ])(
    'invalidates verified and pending previews when %s changes',
    async (selector, first, second) => {
      let finishPreview!: (value: unknown) => void
      let previews = 0
      vi.mocked(callOperation).mockImplementation(async (name) => {
        if (name === 'finance.record.get') return { data: knownDetail }
        if (++previews === 1) return previewResult
        return new Promise((resolve) => {
          finishPreview = resolve
        })
      })
      const panel = mountPanel()
      await flushPromises()
      await panel.get('[data-test="edit-record"]').trigger('click')
      await panel.get('[data-test="reason"]').setValue('Initial sourced correction')
      await panel.get('[data-test="date"]').setValue('2026-09-01')
      await panel.get('form').trigger('submit')
      await flushPromises()
      expect(panel.find('[data-test="save-edit"]').exists()).toBe(true)
      await panel.get(selector).setValue(first)
      expect(panel.find('[data-test="save-edit"]').exists()).toBe(false)
      await panel.get('form').trigger('submit')
      await flushPromises()
      await panel.get(selector).setValue(second)
      finishPreview(previewResult)
      await flushPromises()
      expect(panel.find('[data-test="save-edit"]').exists()).toBe(false)
      expect(
        vi.mocked(callOperation).mock.calls.some((call) => call[0] === 'finance.record.commit'),
      ).toBe(false)
      panel.unmount()
    },
  )

  it.each([
    ['20', '0', '20'],
    ['20.00', '2', '2000'],
    ['20.125', '3', '20125'],
    ['90071992547409.93', '2', '9007199254740993'],
  ])(
    'preserves entered %s at precision %s without classifying, including reload',
    async (amount, exponent, minor) => {
      vi.mocked(callOperation).mockImplementation(async (name) => {
        if (name === 'finance.record.preview') return previewResult
        if (name === 'finance.record.commit')
          return {
            data: {
              request_id: 'synthetic',
              changed_refs: ['bookkeeping:current'],
              snapshot: { ...snapshot, revision: 2 },
              reread_result: 'verified',
              idempotency_replay: false,
            },
          }
        return {
          data: {
            ...knownDetail,
            amount: { minor, currency: 'USD', exponent: Number(exponent) },
            record: { ...knownDetail.record, amount: { amount_minor: minor, currency: 'USD' } },
          },
        }
      })
      const panel = mountPanel({ recordRef: null })
      await flushPromises()
      await panel.get('[data-test="date"]').setValue('2026-10-01')
      await panel.get('[data-test="amount"]').setValue(amount)
      await panel.get('input[maxlength="1"]').setValue(exponent)
      await panel.get('[data-test="reason"]').setValue('Source-supported synthetic entry')
      await panel.get('form').trigger('submit')
      await flushPromises()
      const intent = vi
        .mocked(callOperation)
        .mock.calls.find((c) => c[0] === 'finance.record.preview')![1]
      expect((intent.fields as Record<string, unknown>).amount).toEqual({
        minor,
        currency: 'USD',
        exponent: Number(exponent),
      })
      expect(intent.fields).not.toHaveProperty('reporting')
      await panel.get('[data-test="save-edit"]').trigger('click')
      await flushPromises()
      expect(
        vi.mocked(callOperation).mock.calls.find((c) => c[0] === 'finance.record.commit')![1]
          .fields,
      ).toEqual(intent.fields)
      await panel.setProps({
        recordRef: 'bookkeeping:current',
        snapshot: { ...snapshot, revision: 2 },
        exponent: null,
      })
      await flushPromises()
      expect(panel.text()).not.toContain('precision unavailable')
      await panel.get('[data-test="edit-record"]').trigger('click')
      expect((panel.get('[data-test="amount"]').element as HTMLInputElement).value).toBe(amount)
      panel.unmount()
    },
  )

  it('prefers fresh detail over stale row precision and keeps exact correction money', async () => {
    let saved = false
    vi.mocked(callOperation).mockImplementation(async (name) => {
      if (name === 'finance.record.get')
        return {
          data: saved
            ? { ...knownDetail, amount: { minor: '2125', currency: 'USD', exponent: 2 } }
            : knownDetail,
        }
      if (name === 'finance.record.commit') {
        saved = true
        return {
          data: {
            request_id: 'synthetic',
            changed_refs: ['bookkeeping:current'],
            snapshot: { ...snapshot, revision: 2 },
            reread_result: 'verified',
            idempotency_replay: false,
          },
        }
      }
      return previewResult
    })
    const panel = mountPanel({ exponent: 3 })
    await flushPromises()
    expect(panel.text()).toContain('USD 20.00')
    await panel.get('[data-test="edit-record"]').trigger('click')
    expect((panel.get('[data-test="amount"]').element as HTMLInputElement).value).toBe('20.00')
    await panel.get('[data-test="amount"]').setValue('21.25')
    await panel.get('[data-test="reason"]').setValue('Correct exact source amount')
    await panel.get('form').trigger('submit')
    await flushPromises()
    expect(
      (vi.mocked(callOperation).mock.calls.at(-1)![1].fields as Record<string, unknown>).amount,
    ).toEqual({ minor: '2125', currency: 'USD', exponent: 2 })
    await panel.get('[data-test="save-edit"]').trigger('click')
    await flushPromises()
    await panel.setProps({ snapshot: { ...snapshot, revision: 2 } })
    await flushPromises()
    expect(panel.text()).toContain('USD 21.25')
    expect(panel.text()).not.toContain('USD 20.00')
    panel.unmount()
  })

  it.each([2, null])(
    'preserves raw minor units for precision-only confirmation from %s and cancel',
    async (exponent) => {
      vi.mocked(callOperation).mockImplementation(async (name) =>
        name === 'finance.record.get'
          ? { data: { ...knownDetail, amount: { ...knownDetail.amount, exponent } } }
          : previewResult,
      )
      const panel = mountPanel({ exponent: 6 })
      await flushPromises()
      await panel.get('[data-test="edit-record"]').trigger('click')
      expect((panel.get('input[maxlength="1"]').element as HTMLInputElement).value).toBe(
        exponent === null ? '' : '2',
      )
      await panel.get('input[maxlength="1"]').setValue('3')
      await panel.get('[data-test="reason"]').setValue('Confirm source precision only')
      await panel.get('form').trigger('submit')
      await flushPromises()
      expect(
        (vi.mocked(callOperation).mock.calls.at(-1)![1].fields as Record<string, unknown>).amount,
      ).toEqual({ minor: '2000', currency: 'USD', exponent: 3 })
      expect(vi.mocked(callOperation).mock.calls.at(-1)![1].fields).not.toHaveProperty('reporting')
      await panel.get('[data-test="cancel-edit"]').trigger('click')
      expect(
        vi.mocked(callOperation).mock.calls.some((c) => c[0] === 'finance.record.commit'),
      ).toBe(false)
      panel.unmount()
    },
  )

  it('does not re-adopt an existing classification when only display precision changes', async () => {
    vi.mocked(callOperation).mockImplementation(async (name) =>
      name === 'finance.record.get' ? { data: knownDetail } : previewResult,
    )
    const panel = mountPanel({
      reporting: {
        treatment: 'expense',
        economic_event_ref: 'event:one',
        component_ref: 'component:one',
        currency_exponent: 2,
      },
    })
    await flushPromises()
    await panel.get('[data-test="edit-record"]').trigger('click')
    await panel.get('input[maxlength="1"]').setValue('3')
    await panel.get('[data-test="reason"]').setValue('Confirm formatting, classification unchanged')
    await panel.get('form').trigger('submit')
    await flushPromises()
    expect(vi.mocked(callOperation).mock.calls.at(-1)![1].fields).toEqual({
      amount: { minor: '2000', currency: 'USD', exponent: 3 },
    })
    panel.unmount()
  })

  it('keeps legacy precision unknown despite stale props and requires confirmation for entered money', async () => {
    vi.mocked(callOperation).mockResolvedValue({
      data: { ...knownDetail, amount: { ...knownDetail.amount, exponent: null } },
    })
    const panel = mountPanel({ exponent: 2 })
    await flushPromises()
    expect(panel.text()).toContain('USD 2000 minor units (precision unavailable)')
    await panel.get('[data-test="edit-record"]').trigger('click')
    expect((panel.get('[data-test="amount"]').element as HTMLInputElement).value).toBe('')
    await panel.get('[data-test="amount"]').setValue('20.00')
    await panel.get('[data-test="reason"]').setValue('Synthetic correction')
    await panel.get('form').trigger('submit')
    await flushPromises()
    expect(panel.get('[role="alert"]').text()).toContain('Confirm currency precision')
    expect(vi.mocked(callOperation).mock.calls).toHaveLength(1)
    panel.unmount()
  })

  it('renders ordered A/B history, exact references and missing stored actor/time separately from current status', async () => {
    const history = [
      {
        correction_ref: 'correction:z',
        status: 'applied',
        superseded_ref: 'receipt:legacy',
        replacement_ref: 'bookkeeping:A',
        reason: 'A <source> correction',
        actor: 'operator:A',
        at: '2026-10-01T10:00:00Z',
        decision_ref: 'decision:A',
        evidence_refs: ['source:A'],
        source_refs: ['source:A'],
      },
      {
        correction_ref: 'correction:a',
        status: 'applied',
        superseded_ref: 'bookkeeping:A',
        replacement_ref: 'bookkeeping:current',
        reason: 'B source correction',
        actor: null,
        at: null,
        decision_ref: null,
        evidence_refs: ['source:B'],
        source_refs: ['source:B'],
      },
    ]
    vi.mocked(callOperation).mockResolvedValue({
      data: {
        ...knownDetail,
        lineage: [
          'receipt:legacy',
          'correction:z',
          'bookkeeping:A',
          'correction:a',
          'bookkeeping:current',
        ],
        correction_history: history,
        record: {
          ...knownDetail.record,
          status_history: [
            {
              at: '2026-10-03',
              actor: 'status:actor',
              to_status: 'prepared',
              reason: 'Current prepared status',
            },
          ],
        },
      },
    })
    const panel = mountPanel()
    await flushPromises()
    await panel.get('details > summary').trigger('click')
    const rows = panel.findAll('[data-test="correction-history-item"]')
    expect(rows).toHaveLength(2)
    expect(rows[0]!.text()).toContain('A <source> correction')
    for (const value of [
      'operator:A',
      '2026-10-01T10:00:00Z',
      'decision:A',
      'source:A',
      'receipt:legacy',
      'bookkeeping:A',
    ])
      expect(rows[0]!.text()).toContain(value)
    expect(rows[0]!.text()).not.toContain('Current record')
    expect(rows[1]!.text()).toContain('B source correction')
    expect(rows[1]!.text()).toContain('Actor unavailable')
    expect(rows[1]!.text()).toContain('Application time unavailable')
    expect(rows[1]!.text()).toContain('Current record')
    expect(rows[1]!.text()).not.toContain('status:actor')
    expect(panel.text()).toContain('Current record status history')
    expect(panel.text()).toContain('Current prepared status')
    expect(panel.text()).toContain('correction:z')
    expect(panel.text()).toContain('correction:a')
    expect(panel.find('source').exists()).toBe(false)
    await rows[0]!.findAll('button')[0]!.trigger('click')
    await flushPromises()
    expect(vi.mocked(callOperation).mock.calls.at(-1)![0]).toBe('finance.record.get')
    expect(vi.mocked(callOperation).mock.calls.at(-1)![1]).toMatchObject({
      record_ref: 'receipt:legacy',
      project_id: 1,
      workspace_ref: 'finance:project:1',
      access_generation: 1,
      expected_snapshot: { revision: 1, sha256: snapshot.sha256 },
    })
    expect(panel.findAll('[data-test="correction-history-item"]')).toHaveLength(2)
    expect(vi.mocked(callOperation).mock.calls.every((c) => c[0] === 'finance.record.get')).toBe(
      true,
    )
    panel.unmount()
  })

  const focusDetail = {
    ...knownDetail,
    correction_history: [
      {
        correction_ref: 'correction:A',
        status: 'applied',
        superseded_ref: 'bookkeeping:old',
        replacement_ref: 'bookkeeping:current',
        reason: 'Stored correction',
        actor: null,
        at: null,
        decision_ref: null,
        evidence_refs: [],
        source_refs: ['source:synthetic'],
      },
    ],
  }

  it.each(['success', 'error'])(
    'keeps keyboard focus inside the panel while inspecting and after %s',
    async (outcome) => {
      let settle!: (v: unknown) => void
      let fail!: (reason: Error) => void
      vi.mocked(callOperation)
        .mockResolvedValueOnce({ data: focusDetail })
        .mockImplementationOnce(
          () =>
            new Promise((resolve, reject) => {
              settle = resolve
              fail = reject
            }),
        )
      const panel = mountPanel()
      await flushPromises()
      try {
        const button = panel.get('[data-test="correction-history-item"] button')
        ;(button.element as HTMLButtonElement).focus()
        expect(document.activeElement).toBe(button.element)
        await button.trigger('click')
        await flushPromises()
        expect(document.activeElement).not.toBe(document.body)
        expect(panel.element.contains(document.activeElement)).toBe(true)
        expect((document.activeElement as HTMLElement).getAttribute('aria-label')).toBe(
          'Close panel',
        )
        if (outcome === 'success') settle({ data: focusDetail })
        else fail(new Error('Synthetic unavailable source'))
        await flushPromises()
        expect(document.activeElement).not.toBe(document.body)
        expect(panel.element.contains(document.activeElement)).toBe(true)
        if (outcome === 'error')
          expect(panel.get('[role="alert"]').text()).toContain('Record unavailable')
      } finally {
        panel.unmount()
      }
    },
  )

  it.each(['closed', 'unmounted', 'scope'])(
    'does not steal focus after an inspection becomes %s',
    async (state) => {
      let settle!: (v: unknown) => void
      vi.mocked(callOperation)
        .mockResolvedValueOnce({ data: focusDetail })
        .mockImplementationOnce(
          () =>
            new Promise((resolve) => {
              settle = resolve
            }),
        )
        .mockResolvedValue({
          data: {
            ...focusDetail,
            record: { ...focusDetail.record, business_purpose: 'Current scope' },
          },
        })
      const outside = document.createElement('button')
      outside.textContent = 'Other safe control'
      document.body.append(outside)
      const panel = mountPanel()
      await flushPromises()
      const button = panel.get('[data-test="correction-history-item"] button')
      ;(button.element as HTMLButtonElement).focus()
      await button.trigger('click')
      if (state === 'closed') await panel.setProps({ modelValue: false })
      else if (state === 'unmounted') panel.unmount()
      else
        await panel.setProps({
          projectId: 2,
          access: { ...props.access, project_id: 2, generation: 2 },
          snapshot: { ...snapshot, sha256: 'c'.repeat(64) },
        })
      await flushPromises()
      outside.focus()
      settle({
        data: {
          ...focusDetail,
          record: { ...focusDetail.record, business_purpose: 'Stale private scope' },
        },
      })
      await flushPromises()
      expect(document.activeElement).toBe(outside)
      if (state === 'scope') {
        expect(panel.text()).toContain('Current scope')
        expect(panel.text()).not.toContain('Stale private scope')
      }
      if (state !== 'unmounted') panel.unmount()
    },
  )
})
