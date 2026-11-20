import { computed, onBeforeUnmount, ref, type Ref } from 'vue'
import type {
  SchemaFinanceAccessOut,
  SchemaFinanceCommitOut,
  SchemaFinanceEditIntent,
  SchemaFinancePreviewOut,
  SchemaFinanceRecordOut,
  SchemaFinanceSnapshotOut,
} from '@/api'
import { ApiError } from '@/lib/client'
import { callOperation } from '@/lib/operations'
import {
  decimalToMinor,
  minorToDecimal,
  newIntentId,
  object,
  refs,
  snapshotRef,
  text,
} from '@/lib/finance'

export interface FinanceRecordScope {
  projectId: number
  access: SchemaFinanceAccessOut
  authorityCurrent?: boolean
  snapshot: SchemaFinanceSnapshotOut
  recordRef: string | null
}
function emptyDraft() {
  return {
    date: '',
    amount: '',
    currency: 'USD',
    precision: '2',
    merchant: '',
    purpose: '',
    category: '',
    project: '',
    account: '',
    treatment: '',
    event: '',
    component: '',
    sources: '',
    attestation: '',
    reason: '',
  }
}

export type FinanceRecordDraft = ReturnType<typeof emptyDraft>

export function useFinanceRecordEditor(
  props: FinanceRecordScope,
  detail: Ref<SchemaFinanceRecordOut | null>,
  handlers: { saved: (value: SchemaFinanceCommitOut) => void; denied: (cause: unknown) => void },
) {
  const editing = ref(false),
    previewing = ref(false),
    saving = ref(false)
  const error = ref(''),
    notice = ref(''),
    stale = ref(false),
    uncertain = ref(false)
  const preview = ref<SchemaFinancePreviewOut | null>(null)
  const heldIntent = ref<Record<string, unknown> | null>(null)
  const draft = ref(emptyDraft())
  const originalDraft = ref('')
  let active = true,
    editSequence = 0
  const allowed = computed(() =>
    props.recordRef === null
      ? [
          'date',
          'amount',
          'category',
          'business_purpose',
          'external_project_ref',
          'reporting',
          'account_ref',
        ]
      : (detail.value?.editable_fields ?? []),
  )
  const canEdit = computed(
    () =>
      props.access.modes.includes('correct') &&
      allowed.value.length > 0 &&
      (props.recordRef === null ||
        ['receipts', 'bookkeeping'].includes(detail.value?.collection ?? '')),
  )
  const base = () => ({
    project_id: props.projectId,
    workspace_ref: props.access.workspace_ref,
    access_generation: props.access.generation,
  })
  const currentAmount = computed(
    () => detail.value?.amount ?? { ...object(detail.value?.record.amount), exponent: null },
  )
  function cancelEdit() {
    editSequence++
    editing.value = false
    previewing.value = false
    preview.value = null
    heldIntent.value = null
    error.value = ''
    uncertain.value = false
    stale.value = false
    draft.value = emptyDraft()
  }
  function beginEdit() {
    if (!canEdit.value || props.authorityCurrent === false) return
    editSequence++
    notice.value = ''
    error.value = ''
    stale.value = false
    uncertain.value = false
    const record = detail.value?.record ?? {},
      money = object(currentAmount.value),
      reporting = object(detail.value?.reporting)
    const exponent = props.recordRef === null ? 2 : money.exponent
    const minor = text(money.minor) || text(money.amount_minor)
    draft.value = {
      date: text(record.business_date) || text(record.date),
      amount: typeof exponent === 'number' && minor ? minorToDecimal(minor, exponent) : '',
      currency: text(money.currency) || 'USD',
      precision: typeof exponent === 'number' ? String(exponent) : '',
      merchant: text(record.merchant),
      purpose: text(record.business_purpose),
      category: text(object(record.category).category_ref),
      project: text(record.external_project_ref),
      account: text(record.account_ref),
      treatment: text(reporting.treatment),
      event: text(reporting.economic_event_ref),
      component: text(reporting.component_ref),
      sources: (detail.value?.source_refs ?? []).join('\n'),
      attestation: '',
      reason: '',
    }
    originalDraft.value = JSON.stringify(draft.value)
    preview.value = null
    heldIntent.value = null
    editing.value = true
  }
  function invalidateDraft() {
    editSequence++
    preview.value = null
    heldIntent.value = null
    previewing.value = false
    uncertain.value = false
  }
  function buildIntent(): Record<string, unknown> {
    const d = draft.value,
      initial = JSON.parse(originalDraft.value) as typeof d
    const fields: Partial<SchemaFinanceEditIntent['fields']> = {}
    const creating = props.recordRef === null
    const precisionOnly =
      !creating &&
      d.precision !== initial.precision &&
      d.amount === initial.amount &&
      d.currency === initial.currency
    const has = (key: string) => allowed.value.includes(key)
    if ((has('date') || has('business_date')) && (creating || d.date !== initial.date))
      fields.date = d.date
    if (
      has('amount') &&
      (creating ||
        d.amount !== initial.amount ||
        d.currency !== initial.currency ||
        d.precision !== initial.precision)
    ) {
      const exponent = /^[0-6]$/.test(d.precision) ? Number(d.precision) : null
      if (exponent === null)
        throw new Error('Confirm currency precision before changing an amount.')
      const money = object(currentAmount.value)
      fields.amount = {
        minor: precisionOnly
          ? text(money.minor) || text(money.amount_minor)
          : decimalToMinor(d.amount, exponent),
        currency: d.currency.toUpperCase(),
        exponent,
      }
    }
    if (has('merchant') && d.merchant !== initial.merchant) fields.merchant = d.merchant
    if (
      has('business_purpose') &&
      (creating ? Boolean(d.purpose.trim()) : d.purpose !== initial.purpose)
    )
      fields.business_purpose = d.purpose
    if (has('category') && (creating || d.category !== initial.category))
      fields.category = d.category
        ? { state: 'reviewed', category_ref: d.category }
        : { state: 'unresolved' }
    if (has('external_project_ref') && d.project !== initial.project) {
      if (!d.project.trim())
        throw new Error(
          'Removing an existing project assignment requires an explicit external correction workflow.',
        )
      fields.external_project_ref = d.project
    }
    if (has('account_ref') && (creating || d.account !== initial.account) && d.account)
      fields.account_ref = d.account
    const sources = d.sources
      .split(/[\n,]/)
      .map((s) => s.trim())
      .filter(Boolean)
    const classificationChanged =
      d.treatment !== initial.treatment ||
      d.event !== initial.event ||
      d.component !== initial.component
    if (has('reporting') && d.treatment && (!precisionOnly || classificationChanged)) {
      if (!sources.length || !d.event || !d.component || !/^\d$/.test(d.precision))
        throw new Error(
          'Classification needs supporting sources, transaction/component identity and confirmed currency precision.',
        )
      const reporting = object(detail.value?.reporting),
        reportingSources = refs(reporting.source_refs)
      fields.reporting = {
        ...reporting,
        economic_event_ref: d.event,
        component_ref: d.component,
        treatment: d.treatment,
        recognition_basis: text(reporting.recognition_basis) || 'cash',
        date_basis: text(reporting.date_basis) || 'transaction',
        currency_exponent: Number(d.precision),
        source_refs:
          d.sources === initial.sources && reportingSources.length ? reportingSources : sources,
      }
    }
    if (!Object.keys(fields).length) throw new Error('Change a supported fact before previewing.')
    if (!d.reason.trim()) throw new Error('Explain the source correction or entry.')
    return {
      ...base(),
      request_id: newIntentId(),
      expected_snapshot: snapshotRef(props.snapshot),
      action: creating
        ? 'create-bookkeeping'
        : detail.value?.collection === 'receipts'
          ? 'correct-receipt-facts'
          : 'correct-bookkeeping',
      ...(creating ? {} : { record_ref: detail.value?.current_ref }),
      fields,
      source_refs: sources,
      ...(d.attestation ? { attestation: d.attestation } : {}),
      reason: d.reason,
    }
  }
  async function requestPreview() {
    if (!canEdit.value || props.authorityCurrent === false) return
    error.value = ''
    preview.value = null
    heldIntent.value = null
    let intent: Record<string, unknown>
    try {
      intent = buildIntent()
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Check the supplied facts.'
      return
    }
    const sequence = ++editSequence
    previewing.value = true
    try {
      const result = await callOperation<{ data: SchemaFinancePreviewOut }>(
        'finance.record.preview',
        { ...intent, response_mode: 'compact' },
      )
      if (!active || sequence !== editSequence || !editing.value || !props.authorityCurrent) return
      if (
        result.data.state !== 'valid' ||
        result.data.source_snapshot.sha256 !== props.snapshot.sha256
      )
        throw new Error('Source changed. Refresh before previewing again.')
      preview.value = result.data
      heldIntent.value = intent
    } catch (cause) {
      if (active && sequence === editSequence) {
        handlers.denied(cause)
        error.value =
          'Preview failed or facts are unsupported. Check the sources and refresh if the snapshot changed.'
      }
    } finally {
      if (active && sequence === editSequence) previewing.value = false
    }
  }
  async function save() {
    if (
      !canEdit.value ||
      props.authorityCurrent === false ||
      !preview.value ||
      !heldIntent.value ||
      stale.value ||
      saving.value
    )
      return
    const sequence = editSequence
    const intent = heldIntent.value,
      digest = preview.value.preview_digest
    saving.value = true
    error.value = ''
    try {
      const result = await callOperation<{ data: SchemaFinanceCommitOut }>(
        'finance.record.commit',
        {
          ...intent,
          preview_digest: digest,
          idempotency_key: intent.request_id,
          response_mode: 'compact',
        },
      )
      if (!active || sequence !== editSequence) return
      cancelEdit()
      notice.value = 'Changes saved.'
      handlers.saved(result.data)
    } catch (e) {
      if (!active || sequence !== editSequence) return
      handlers.denied(e)
      const code = e instanceof ApiError ? text(object(e.data).finance_code) : 'outcome_unknown'
      uncertain.value = code === 'outcome_unknown'
      stale.value = !uncertain.value
      error.value = uncertain.value
        ? 'Save outcome is uncertain. Recheck this same save to reconcile its existing request; do not create another entry.'
        : 'Save blocked or source changed. Refresh and review current facts before another save.'
    } finally {
      if (active) saving.value = false
    }
  }

  function suspend() {
    if (!saving.value && !uncertain.value) invalidateDraft()
  }
  onBeforeUnmount(() => {
    active = false
    editSequence++
    heldIntent.value = null
  })
  return {
    draft,
    editing,
    previewing,
    saving,
    error,
    notice,
    stale,
    uncertain,
    preview,
    allowed,
    canEdit,
    currentAmount,
    base,
    cancelEdit,
    beginEdit,
    invalidateDraft,
    requestPreview,
    save,
    suspend,
  }
}
