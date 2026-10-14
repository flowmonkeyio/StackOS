<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
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
  decimalToMinor,
  formatMoney,
  minorToDecimal,
  newIntentId,
  object,
  objects,
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
const facts = computed(() => [
  {
    label: 'Date',
    value: text(detail.value?.record.business_date) || text(detail.value?.record.date) || 'Undated',
  },
  { label: 'Vendor', value: text(detail.value?.record.merchant) || 'Not supplied' },
  {
    label: 'Amount',
    value: formatMoney({ ...object(detail.value?.record.amount), exponent: props.exponent }),
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
    money = object(record.amount),
    reporting = props.reporting ?? {}
  const exponent = props.recordRef === null ? 2 : props.exponent
  draft.value = {
    date: text(record.business_date) || text(record.date),
    amount:
      typeof exponent === 'number' && text(money.amount_minor)
        ? minorToDecimal(text(money.amount_minor), exponent)
        : '',
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
watch(
  draft,
  () => {
    editSequence++
    preview.value = null
    heldIntent.value = null
    previewing.value = false
    uncertain.value = false
  },
  { deep: true, flush: 'sync' },
)
watch(
  () => [
    props.modelValue,
    props.projectId,
    props.access.workspace_ref,
    props.access.generation,
    props.recordRef,
    props.snapshot.sha256,
  ],
  async () => {
    const sequence = ++loadSequence
    cancelEdit()
    clearOriginal()
    detail.value = null
    notice.value = ''
    loading.value = false
    if (!props.modelValue) return
    if (props.recordRef === null) {
      beginEdit()
      return
    }
    loading.value = true
    try {
      const result = await callOperation<{ data: SchemaFinanceRecordOut }>('finance.record.get', {
        ...base(),
        record_ref: props.recordRef,
        expected_snapshot: snapshotRef(props.snapshot),
        response_mode: 'compact',
      })
      if (!active || sequence !== loadSequence) return
      detail.value = result.data
    } catch {
      if (active && sequence === loadSequence)
        error.value = 'Record unavailable or source changed. Refresh the workspace before editing.'
    } finally {
      if (active && sequence === loadSequence) loading.value = false
    }
  },
  { immediate: true },
)

function buildIntent(): Record<string, unknown> {
  const d = draft.value,
    initial = JSON.parse(originalDraft.value) as typeof d
  const fields: Partial<SchemaFinanceEditIntent['fields']> = {}
  const creating = props.recordRef === null
  const has = (key: string) => allowed.value.includes(key)
  if ((has('date') || has('business_date')) && (creating || d.date !== initial.date))
    fields.date = d.date
  if (
    has('amount') &&
    (creating || d.amount !== initial.amount || d.currency !== initial.currency)
  ) {
    const exponent = /^\d$/.test(d.precision) ? Number(d.precision) : null
    fields.amount = {
      minor: decimalToMinor(d.amount, exponent),
      currency: d.currency.toUpperCase(),
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
  if (has('reporting') && d.treatment) {
    if (!sources.length || !d.event || !d.component || !/^\d$/.test(d.precision))
      throw new Error(
        'Classification needs supporting sources, transaction/component identity and confirmed currency precision.',
      )
    fields.reporting = {
      ...props.reporting,
      economic_event_ref: d.event,
      component_ref: d.component,
      treatment: d.treatment,
      recognition_basis: text(props.reporting?.recognition_basis) || 'cash',
      date_basis: text(props.reporting?.date_basis) || 'transaction',
      currency_exponent: Number(d.precision),
      source_refs: sources,
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
async function showOriginal(attachment: string) {
  clearOriginal()
  const sequence = evidenceSequence
  evidenceState.value = 'Loading original…'
  try {
    const value = await callOperation<SchemaFinanceContentOut>('finance.evidence.read', {
      ...base(),
      attachment_ref: attachment,
      expected_snapshot: snapshotRef(props.snapshot),
      response_mode: 'raw',
    })
    if (!active || sequence !== evidenceSequence) return
    if (value.state !== 'available' || !value.content_base64) {
      evidenceState.value = `Original ${value.state.replaceAll('_', ' ')}.`
      return
    }
    evidenceName.value = value.filename || 'original'
    evidenceType.value = value.inline_allowed ? value.content_type || '' : ''
    evidenceURL.value = URL.createObjectURL(
      contentBlob(value.content_base64, value.content_type || 'application/octet-stream'),
    )
    evidenceState.value = `Verified original · ${value.sha256 || ''}`
  } catch {
    if (active && sequence === evidenceSequence)
      evidenceState.value = 'Original unavailable or source changed. Refresh to inspect custody.'
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
      <section class="mt-5 space-y-2">
        <h3 class="t-h3">Original evidence</h3>
        <p v-if="!detail.evidence.length" class="text-sm text-fg-muted">
          No retained original is linked. Source facts and missing custody remain distinct.
        </p>
        <UiButton
          v-for="evidence in detail.evidence"
          :key="text(evidence.attachment_ref)"
          size="sm"
          @click="showOriginal(text(evidence.attachment_ref))"
          >View {{ text(evidence.attachment_ref) }} · {{ text(evidence.state) }}</UiButton
        >
        <p v-if="evidenceState" role="status" class="break-all text-xs text-fg-muted">
          {{ evidenceState }}
        </p>
        <template v-if="evidenceURL">
          <a :href="evidenceURL" :download="evidenceName" class="focus-ring text-fg-link"
            >Download verified original</a
          >
          <img
            v-if="evidenceType.startsWith('image/')"
            :src="evidenceURL"
            alt="Verified original evidence"
            class="max-w-full"
          />
          <iframe
            v-else-if="evidenceType === 'application/pdf'"
            :src="evidenceURL"
            sandbox=""
            title="Verified original evidence"
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
        <p v-for="item in objects(detail.lineage)" :key="text(item.record_id)">
          {{ text(item.record_id) }} · {{ text(item.status) }}
        </p>
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
              data-test="date"
              type="date"
              min="0001-01-01"
              max="9999-12-31"
          /></UiFormField>
          <UiFormField v-if="allowed.includes('merchant')" label="Vendor"
            ><UiInput v-model="draft.merchant"
          /></UiFormField>
          <UiFormField
            v-if="allowed.includes('amount')"
            label="Amount"
            help="An empty amount keeps the existing fact. Confirm precision before changing it."
            ><UiInput v-model="draft.amount" inputmode="decimal" data-test="amount"
          /></UiFormField>
          <UiFormField v-if="allowed.includes('amount')" label="Currency"
            ><UiInput v-model="draft.currency" maxlength="3"
          /></UiFormField>
          <UiFormField
            v-if="allowed.includes('amount')"
            label="Currency decimal places"
            :help="
              draft.precision
                ? 'Explicit precision for this entry; currencies remain separate.'
                : 'Source precision unavailable. Confirm it to change the amount.'
            "
            ><UiInput v-model="draft.precision" inputmode="numeric" maxlength="1"
          /></UiFormField>
          <UiFormField
            v-if="allowed.includes('category')"
            label="Category reference"
            help="Use an existing configured category; blank remains unresolved."
            ><UiInput v-model="draft.category"
          /></UiFormField>
          <UiFormField v-if="allowed.includes('account_ref')" label="Account reference"
            ><UiInput v-model="draft.account"
          /></UiFormField>
          <UiFormField
            v-if="allowed.includes('external_project_ref')"
            label="Project reference"
            help="Leave shared or company-wide costs unassigned."
            ><UiInput v-model="draft.project"
          /></UiFormField>
        </div>
        <UiFormField v-if="allowed.includes('business_purpose')" label="Business purpose"
          ><UiTextarea v-model="draft.purpose"
        /></UiFormField>
        <UiFormField
          label="Supporting source references"
          help="Existing source references, one per line. Corrections require evidence."
          ><UiTextarea v-model="draft.sources"
        /></UiFormField>
        <UiFormField
          v-if="recordRef === null"
          label="Operator attestation"
          help="Needed if no existing source supports the entry; retained original remains missing."
          ><UiTextarea v-model="draft.attestation"
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
            ><UiInput v-model="draft.event"
          /></UiFormField>
          <UiFormField
            label="Component identity"
            help="Distinguish principal, fees and refunds without counting a representation twice."
            ><UiInput v-model="draft.component"
          /></UiFormField>
        </details>
        <UiFormField label="Reason" required
          ><UiTextarea v-model="draft.reason" data-test="reason"
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
