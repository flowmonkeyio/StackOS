<script setup lang="ts">
import { computed, onBeforeUnmount, onBeforeUpdate, ref } from 'vue'
import type {
  SchemaFinanceAccessOut,
  SchemaFinanceCommitOut,
  SchemaFinanceContentOut,
  SchemaFinanceEditIntent,
  SchemaFinancePreviewOut,
  SchemaFinanceRecordOut,
  SchemaFinanceSnapshotOut,
} from '@/api'
import {
  UiButton,
  UiCallout,
  UiDescriptionList,
  UiFormField,
  UiInput,
  UiSelect,
  UiSidePanel,
  UiTextarea,
} from '@/components/ui'
import { ApiError } from '@/lib/client'
import { callOperation } from '@/lib/operations'
import {
  contentBlob,
  canReadDocument,
  decimalToMinor,
  documentAvailabilityText,
  documentGroups,
  documentName,
  formatMoney,
  minorToDecimal,
  newIntentId,
  object,
  objects,
  refs,
  snapshotRef,
  text,
} from '@/lib/finance'

const props = defineProps<{
  modelValue: boolean
  projectId: number
  access: SchemaFinanceAccessOut
  snapshot: SchemaFinanceSnapshotOut
  recordRef: string | null
  reporting?: Record<string, unknown>
  exponent?: number | null
}>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'saved', value: SchemaFinanceCommitOut): void
  (e: 'refresh'): void
}>()
const detail = ref<SchemaFinanceRecordOut | null>(null)
const loading = ref(false),
  editing = ref(false),
  previewing = ref(false),
  saving = ref(false)
const error = ref(''),
  notice = ref(''),
  stale = ref(false),
  uncertain = ref(false)
const preview = ref<SchemaFinancePreviewOut | null>(null)
const heldIntent = ref<Record<string, unknown> | null>(null)
const draft = ref({
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
})
const originalDraft = ref('')
const evidenceURL = ref(''),
  evidenceType = ref(''),
  evidenceName = ref(''),
  evidenceState = ref('')
const selectedAttachment = ref(''),
  selectedOriginalName = ref(''),
  originalReadFailed = ref(false)
const originalGroups = computed(() =>
  documentGroups(detail.value?.evidence, detail.value?.document_summary),
)
let active = true,
  loadSequence = 0,
  editSequence = 0,
  evidenceSequence = 0
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
const facts = computed(() => [
  {
    label: 'Date',
    value: text(detail.value?.record.business_date) || text(detail.value?.record.date) || 'Undated',
  },
  { label: 'Vendor', value: text(detail.value?.record.merchant) || 'Not supplied' },
  {
    label: 'Amount',
    value: formatMoney(currentAmount.value),
  },
  {
    label: 'Business purpose',
    value: text(detail.value?.record.business_purpose) || 'Not supplied',
  },
  { label: 'State', value: text(detail.value?.record.status) || 'Prepared/unposted' },
  { label: 'Current record', value: detail.value?.current_ref ?? '' },
])
function clearOriginal() {
  evidenceSequence++
  if (evidenceURL.value) URL.revokeObjectURL(evidenceURL.value)
  evidenceURL.value = ''
  evidenceType.value = ''
  evidenceName.value = ''
  evidenceState.value = ''
  selectedAttachment.value = ''
  selectedOriginalName.value = ''
  originalReadFailed.value = false
}
function cancelEdit() {
  editSequence++
  editing.value = false
  previewing.value = false
  preview.value = null
  heldIntent.value = null
  error.value = ''
  uncertain.value = false
  stale.value = false
  draft.value = {
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
function close(value: boolean) {
  if (saving.value || uncertain.value) return
  cancelEdit()
  clearOriginal()
  emit('update:modelValue', value)
}
function beginEdit() {
  if (!canEdit.value) return
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
let inspectedScope = ''
function synchronizeScope() {
  const scope = JSON.stringify([
    props.modelValue,
    props.projectId,
    props.access.workspace_ref,
    props.access.generation,
    props.recordRef,
    props.snapshot.revision,
    props.snapshot.sha256,
  ])
  if (scope === inspectedScope) return
  inspectedScope = scope
  loadSequence++
  cancelEdit()
  clearOriginal()
  detail.value = null
  notice.value = ''
  loading.value = false
  if (!props.modelValue) return
  if (props.recordRef === null) beginEdit()
  else void inspectRecord(props.recordRef)
}
synchronizeScope()
onBeforeUpdate(synchronizeScope)

async function inspectRecord(recordRef: string, inspectionEvent?: Event) {
  if (!active || !props.modelValue || saving.value || uncertain.value) return
  const origin = inspectionEvent?.currentTarget
  if (origin instanceof HTMLElement && document.activeElement === origin) {
    // Preserve keyboard position before replacing the focused history control.
    origin.closest('[role="dialog"]')?.querySelector<HTMLElement>('[data-autofocus]')?.focus()
  }
  const sequence = ++loadSequence
  cancelEdit()
  clearOriginal()
  detail.value = null
  notice.value = ''
  loading.value = true
  try {
    const result = await callOperation<{ data: SchemaFinanceRecordOut }>('finance.record.get', {
      ...base(),
      record_ref: recordRef,
      expected_snapshot: snapshotRef(props.snapshot),
      response_mode: 'compact',
    })
    if (!active || sequence !== loadSequence) return
    detail.value = result.data
  } catch {
    if (active && sequence === loadSequence)
      error.value =
        'Record unavailable or source changed. Refresh project Finance data before editing.'
  } finally {
    if (active && sequence === loadSequence) loading.value = false
  }
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
    if (exponent === null) throw new Error('Confirm currency precision before changing an amount.')
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
    if (!active || sequence !== editSequence || !editing.value) return
    if (
      result.data.state !== 'valid' ||
      result.data.source_snapshot.sha256 !== props.snapshot.sha256
    )
      throw new Error('Source changed. Refresh before previewing again.')
    preview.value = result.data
    heldIntent.value = intent
  } catch {
    if (active && sequence === editSequence)
      error.value =
        'Preview failed or facts are unsupported. Check the sources and refresh if the snapshot changed.'
  } finally {
    if (active && sequence === editSequence) previewing.value = false
  }
}
async function save() {
  if (!preview.value || !heldIntent.value || stale.value || saving.value) return
  const sequence = editSequence
  const intent = heldIntent.value,
    digest = preview.value.preview_digest
  saving.value = true
  error.value = ''
  try {
    const result = await callOperation<{ data: SchemaFinanceCommitOut }>('finance.record.commit', {
      ...intent,
      preview_digest: digest,
      idempotency_key: intent.request_id,
      response_mode: 'compact',
    })
    if (!active || sequence !== editSequence) return
    cancelEdit()
    notice.value = `Saved and read back at revision ${result.data.snapshot.revision}.`
    emit('saved', result.data)
  } catch (e) {
    if (!active || sequence !== editSequence) return
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
async function showOriginal(document: Record<string, unknown>) {
  if (!active || !props.modelValue || !canReadDocument(document)) return
  clearOriginal()
  const sequence = evidenceSequence
  const identity = JSON.stringify([base(), props.recordRef, snapshotRef(props.snapshot)])
  const current = () =>
    active &&
    props.modelValue &&
    sequence === evidenceSequence &&
    identity === JSON.stringify([base(), props.recordRef, snapshotRef(props.snapshot)])
  selectedAttachment.value = text(document.attachment_ref)
  selectedOriginalName.value = documentName(document)
  evidenceState.value = `Opening ${selectedOriginalName.value}…`
  try {
    const value = await callOperation<SchemaFinanceContentOut>('finance.evidence.read', {
      ...base(),
      attachment_ref: selectedAttachment.value,
      expected_snapshot: snapshotRef(props.snapshot),
      response_mode: 'raw',
    })
    if (!current()) return
    if (value.state !== 'available' || !value.content_base64) {
      originalReadFailed.value = true
      evidenceState.value =
        value.state === 'too_large'
          ? `${selectedOriginalName.value} cannot be opened here because it exceeds the reader limit. Ask your finance agent to inspect the original in the authorized finance workflow, then refresh.`
          : `${selectedOriginalName.value} cannot be opened. The original is unavailable or the source or access changed. Refresh to inspect current custody.`
      return
    }
    evidenceName.value = value.filename || 'original'
    evidenceType.value = value.inline_allowed ? value.content_type || '' : ''
    evidenceURL.value = URL.createObjectURL(
      contentBlob(value.content_base64, value.content_type || 'application/octet-stream'),
    )
    evidenceState.value = `Opened ${selectedOriginalName.value} · verified original`
  } catch {
    if (current()) {
      originalReadFailed.value = true
      evidenceState.value = `${selectedOriginalName.value} cannot be opened. The original is unavailable or the source or access changed. Refresh to inspect current custody.`
    }
  }
}
onBeforeUnmount(() => {
  active = false
  loadSequence++
  editSequence++
  clearOriginal()
  detail.value = null
  heldIntent.value = null
})
</script>

<template>
  <UiSidePanel
    :model-value="modelValue"
    :title="recordRef ? 'Finance record' : 'New expense or income'"
    size="xl"
    :hide-close="saving || uncertain"
    :static-backdrop="saving || uncertain"
    @update:model-value="close"
  >
    <p v-if="loading" role="status">Loading current source…</p>
    <UiCallout v-if="error" variant="danger" role="alert">{{ error }}</UiCallout>
    <UiCallout v-if="notice" variant="success" role="status">{{ notice }}</UiCallout>
    <template v-if="detail">
      <UiDescriptionList :items="facts" />
      <section class="mt-5 space-y-3" aria-label="Original documents">
        <h3 class="t-h3">Original evidence</h3>
        <p class="text-sm text-fg-muted">
          Invoice documents the purchase; receipt documents payment. An available file alone does
          not confirm payment or posted books.
        </p>
        <section
          v-for="group in originalGroups"
          :key="group.kind"
          :aria-label="group.label"
          class="space-y-2 border-t border-subtle pt-3"
        >
          <h4 class="font-medium">{{ group.label }} · {{ group.status }}</h4>
          <p v-if="group.counts" class="text-xs text-fg-muted">{{ group.counts }}</p>
          <p v-else-if="group.documents.length" class="text-xs text-fg-muted">
            {{ group.documents.length }} linked original(s) with document kind not identified. Open
            a named original and check its source details before identifying it.
          </p>
          <p v-else class="text-xs text-fg-muted">No unidentified originals listed.</p>
          <ul v-if="group.documents.length" class="space-y-3">
            <li
              v-for="evidence in group.documents"
              :key="text(evidence.attachment_ref)"
              class="space-y-1"
            >
              <p class="break-words font-medium">{{ documentName(evidence) }}</p>
              <p class="text-xs text-fg-muted">{{ documentAvailabilityText(evidence) }}</p>
              <p
                v-if="evidence.media_type || typeof evidence.bytes === 'number'"
                class="break-words text-xs text-fg-muted"
              >
                {{ text(evidence.media_type)
                }}<template v-if="typeof evidence.bytes === 'number'">
                  · {{ evidence.bytes }} bytes</template
                >
              </p>
              <p v-if="evidence.retention_state" class="text-xs text-fg-muted">
                Retention: {{ text(evidence.retention_state).replaceAll('_', ' ') }}
              </p>
              <UiButton
                size="sm"
                :disabled="!canReadDocument(evidence)"
                :aria-label="`${evidence.availability === 'available' ? 'Open' : 'Verify and open'} ${documentName(evidence)}`"
                :data-document-ref="text(evidence.attachment_ref)"
                @click="showOriginal(evidence)"
                >{{
                  evidence.availability === 'available'
                    ? 'Open original'
                    : 'Verify and open original'
                }}</UiButton
              >
              <p
                v-if="selectedAttachment === evidence.attachment_ref && originalReadFailed"
                class="text-xs text-fg-muted"
              >
                Read outcome: cannot open this original. Snapshot availability and retention above
                have not changed.
              </p>
              <details class="text-xs text-fg-muted">
                <summary class="focus-ring cursor-pointer">
                  Source details for {{ documentName(evidence) }}
                </summary>
                <p class="break-all">Attachment: {{ text(evidence.attachment_ref) }}</p>
                <p class="break-all">
                  Declared sources: {{ refs(evidence.source_refs).join(', ') || 'Not supplied' }}
                </p>
                <p
                  v-for="(context, index) in objects(evidence.source_context)"
                  :key="`source-${index}`"
                  class="break-all"
                >
                  {{ context.historical ? 'Historical source' : 'Current source' }}:
                  {{ text(context.source_ref) }} · requested {{ text(context.requested_ref) }} ·
                  current {{ text(context.current_ref) }}
                </p>
                <p
                  v-for="(context, index) in objects(evidence.receipt_context)"
                  :key="`receipt-${index}`"
                  class="break-all"
                >
                  {{ context.historical ? 'Historical receipt record' : 'Current receipt record' }}:
                  {{ text(context.receipt_ref) }} · requested {{ text(context.requested_ref) }} ·
                  current {{ text(context.current_ref) }}
                </p>
                <p v-if="evidence.sha256" class="break-all">SHA-256: {{ text(evidence.sha256) }}</p>
                <p v-if="evidence.state">
                  Recorded custody state: {{ text(evidence.state).replaceAll('_', ' ') }}
                </p>
              </details>
            </li>
          </ul>
        </section>
        <p class="text-xs text-fg-muted">
          To identify or link the correct original, ask your finance agent to use the existing
          receipt intake or finance workflow, then refresh project Finance data. Typed counts cover
          the explicitly identified originals; other linked files are listed separately.
        </p>
        <p v-if="evidenceState" role="status" class="break-words text-sm text-fg-muted">
          {{ evidenceState }}
        </p>
        <UiButton v-if="originalReadFailed" size="sm" @click="emit('refresh')"
          >Refresh original availability</UiButton
        >
        <template v-if="evidenceURL">
          <a :href="evidenceURL" :download="evidenceName" class="focus-ring text-fg-link"
            >Download {{ selectedOriginalName }}</a
          >
          <img
            v-if="evidenceType.startsWith('image/')"
            :src="evidenceURL"
            :alt="`Verified original: ${selectedOriginalName}`"
            class="max-w-full"
          />
          <iframe
            v-else-if="evidenceType === 'application/pdf'"
            :src="evidenceURL"
            sandbox=""
            :title="`Verified original: ${selectedOriginalName}`"
            class="h-96 w-full border border-default"
          />
        </template>
      </section>
      <details class="mt-5 text-sm">
        <summary class="focus-ring cursor-pointer">Correction history and sources</summary>
        <p class="mt-2">
          Original date:
          {{ text(detail.original.business_date) || text(detail.original.date) || 'Undated' }}
        </p>
        <p>Sources: {{ detail.source_refs.join(', ') || 'Not supplied' }}</p>
        <ol v-if="detail.correction_history?.length" class="mt-3 space-y-3">
          <li
            v-for="item in detail.correction_history"
            :key="item.correction_ref"
            data-test="correction-history-item"
            class="space-y-1 break-words rounded-md border border-default p-3"
          >
            <p>{{ item.reason }}</p>
            <p>
              {{ item.at || 'Application time unavailable' }} ·
              {{ item.actor || 'Actor unavailable' }} · {{ item.status }}
            </p>
            <p>
              <UiButton
                size="sm"
                variant="ghost"
                @click="inspectRecord(item.superseded_ref, $event)"
              >
                {{ item.superseded_ref }}
              </UiButton>
              →
              <UiButton
                size="sm"
                variant="ghost"
                @click="inspectRecord(item.replacement_ref, $event)"
              >
                {{ item.replacement_ref }}
              </UiButton>
              <span v-if="item.replacement_ref === detail.current_ref"> · Current record</span>
            </p>
            <p>Correction: {{ item.correction_ref }}</p>
            <p>Decision: {{ item.decision_ref || 'Unavailable' }}</p>
            <p>Evidence: {{ item.evidence_refs.join(', ') || 'Not supplied' }}</p>
            <p>Sources: {{ item.source_refs.join(', ') || 'Not supplied' }}</p>
          </li>
        </ol>
        <p class="mt-3 break-words">
          Lineage references: {{ refs(detail.lineage).join(' → ') || 'Not supplied' }}
        </p>
        <h4 v-if="objects(detail.record.status_history).length" class="mt-3 font-medium">
          Current record status history
        </h4>
        <p v-for="(item, index) in objects(detail.record.status_history)" :key="index">
          {{ text(item.at) }} · {{ text(item.actor) }} · {{ text(item.to_status) }} ·
          {{ text(item.reason) }}
        </p>
      </details>
      <UiButton v-if="canEdit && !editing" data-test="edit-record" class="mt-5" @click="beginEdit"
        >Edit sourced facts</UiButton
      >
      <p v-if="!canEdit" class="mt-3 text-sm text-fg-muted">
        This record is read-only here. Provider or billing decisions use their existing workflow.
      </p>
    </template>
    <form v-if="editing" class="mt-5 space-y-4" @submit.prevent="requestPreview">
      <h3 class="t-h3">{{ recordRef ? 'Sourced correction' : 'Record a new item' }}</h3>
      <fieldset :disabled="saving || uncertain" class="space-y-4">
        <div class="grid grid-cols-2 gap-4">
          <UiFormField
            v-if="allowed.includes('date') || allowed.includes('business_date')"
            label="Date"
            required
            ><UiInput
              v-model="draft.date"
              @update:model-value="invalidateDraft"
              data-test="date"
              type="date"
              min="0001-01-01"
              max="9999-12-31"
          /></UiFormField>
          <UiFormField v-if="allowed.includes('merchant')" label="Vendor"
            ><UiInput v-model="draft.merchant" @update:model-value="invalidateDraft"
          /></UiFormField>
          <UiFormField
            v-if="allowed.includes('amount')"
            label="Amount"
            :help="
              recordRef === null
                ? 'Amount is required. Enter a decimal amount using the selected currency decimal places.'
                : 'Leave the amount unchanged to preserve the existing fact. Confirm precision before changing it.'
            "
            ><UiInput
              v-model="draft.amount"
              @update:model-value="invalidateDraft"
              inputmode="decimal"
              data-test="amount"
          /></UiFormField>
          <UiFormField v-if="allowed.includes('amount')" label="Currency"
            ><UiInput v-model="draft.currency" @update:model-value="invalidateDraft" maxlength="3"
          /></UiFormField>
          <UiFormField
            v-if="allowed.includes('amount')"
            label="Currency decimal places"
            :help="
              draft.precision
                ? 'Explicit precision for this entry; currencies remain separate.'
                : 'Source precision unavailable. Confirm it to change the amount.'
            "
            ><UiInput
              v-model="draft.precision"
              @update:model-value="invalidateDraft"
              inputmode="numeric"
              maxlength="1"
          /></UiFormField>
          <UiFormField
            v-if="allowed.includes('category')"
            label="Category reference"
            help="Use an existing configured category; blank remains unresolved."
            ><UiInput v-model="draft.category" @update:model-value="invalidateDraft"
          /></UiFormField>
          <UiFormField v-if="allowed.includes('account_ref')" label="Account reference"
            ><UiInput v-model="draft.account" @update:model-value="invalidateDraft"
          /></UiFormField>
          <UiFormField
            v-if="allowed.includes('external_project_ref')"
            label="Project reference"
            help="Leave shared or company-wide costs unassigned."
            ><UiInput v-model="draft.project" @update:model-value="invalidateDraft"
          /></UiFormField>
        </div>
        <UiFormField v-if="allowed.includes('business_purpose')" label="Business purpose"
          ><UiTextarea v-model="draft.purpose" @update:model-value="invalidateDraft"
        /></UiFormField>
        <UiFormField
          label="Supporting source references"
          help="Existing source references, one per line. Corrections require evidence."
          ><UiTextarea v-model="draft.sources" @update:model-value="invalidateDraft"
        /></UiFormField>
        <UiFormField
          v-if="recordRef === null"
          label="Operator attestation"
          help="Needed if no existing source supports the entry; retained original remains missing."
          ><UiTextarea v-model="draft.attestation" @update:model-value="invalidateDraft"
        /></UiFormField>
        <details v-if="allowed.includes('reporting')" class="space-y-3">
          <summary class="focus-ring cursor-pointer text-sm">
            Reporting classification (explicit decision)
          </summary>
          <UiFormField
            label="Treatment"
            help="Blank leaves the entry outside recognized totals. Sign alone does not establish treatment."
            ><UiSelect
              v-model="draft.treatment"
              @update:model-value="invalidateDraft"
              :options="[
                { value: '', label: 'Unclassified' },
                { value: 'expense', label: 'Expense' },
                { value: 'revenue', label: 'Income' },
                { value: 'fee', label: 'Fee' },
                { value: 'expense_refund', label: 'Expense refund' },
                { value: 'revenue_refund', label: 'Income refund' },
                { value: 'transfer', label: 'Transfer' },
                { value: 'reserve', label: 'Reserve' },
                { value: 'tax_payment', label: 'Tax payment' },
                { value: 'owner_movement', label: 'Owner movement' },
              ]"
          /></UiFormField>
          <UiFormField
            label="Transaction identity"
            help="Reuse an existing economic event reference only for the same transaction."
            ><UiInput v-model="draft.event" @update:model-value="invalidateDraft"
          /></UiFormField>
          <UiFormField
            label="Component identity"
            help="Distinguish principal, fees and refunds without counting a representation twice."
            ><UiInput v-model="draft.component" @update:model-value="invalidateDraft"
          /></UiFormField>
        </details>
        <UiFormField label="Reason" required
          ><UiTextarea
            v-model="draft.reason"
            @update:model-value="invalidateDraft"
            data-test="reason"
        /></UiFormField>
      </fieldset>
      <section
        v-if="preview"
        class="space-y-2 rounded-md border border-default p-3"
        aria-label="Change preview"
      >
        <h3 class="t-h3">Review before saving</h3>
        <p class="text-sm">
          Source revision {{ preview.source_snapshot.revision }} · {{ preview.state }} ·
          prepared/unposted
        </p>
        <UiDescriptionList
          :items="
            Object.entries(object(preview.diff.fields)).map(([key, value]) => ({
              label: key.replaceAll('_', ' '),
              value:
                key === 'amount'
                  ? formatMoney({
                      ...object(value),
                      exponent: /^\d$/.test(draft.precision) ? Number(draft.precision) : null,
                    })
                  : typeof value === 'string'
                    ? value
                    : key === 'reporting'
                      ? text(object(value).treatment)
                      : text(object(value).category_ref) || text(object(value).state),
            }))
          "
        />
        <p class="break-all text-xs text-fg-muted">Preview {{ preview.preview_digest }}</p>
      </section>
      <div class="flex flex-wrap gap-2">
        <UiButton data-test="cancel-edit" :disabled="saving || uncertain" @click="cancelEdit"
          >Cancel</UiButton
        >
        <UiButton
          v-if="!preview"
          data-test="preview-edit"
          type="submit"
          :loading="previewing"
          :disabled="saving || stale"
          variant="primary"
          >Preview changes</UiButton
        >
        <UiButton
          v-else
          data-test="save-edit"
          :loading="saving"
          :disabled="stale"
          variant="primary"
          @click="save"
          >{{ uncertain ? 'Recheck same save' : 'Save reviewed changes' }}</UiButton
        >
        <UiButton v-if="stale" @click="emit('refresh')">Refresh current source</UiButton>
      </div>
    </form>
  </UiSidePanel>
</template>
