<script setup lang="ts">
import { computed, onBeforeUnmount, onBeforeUpdate, ref } from 'vue'
import type { SchemaFinanceCommitOut, SchemaFinanceRecordOut } from '@/api'
import { UiButton, UiCallout, UiDescriptionList, UiSidePanel } from '@/components/ui'
import { ApiError } from '@/lib/client'
import { callOperation } from '@/lib/operations'
import { formatMoney, object, snapshotRef, text } from '@/lib/finance'
import FinanceRecordDocuments from './FinanceRecordDocuments.vue'
import FinanceRecordForm from './FinanceRecordForm.vue'
import FinanceRecordHistory from './FinanceRecordHistory.vue'
import { useFinanceRecordEditor, type FinanceRecordScope } from './useFinanceRecordEditor'

const props = withDefaults(
  defineProps<
    FinanceRecordScope & {
      modelValue: boolean
      reporting?: Record<string, unknown>
      exponent?: number | null
    }
  >(),
  { authorityCurrent: true, reporting: undefined, exponent: undefined },
)
const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'saved', value: SchemaFinanceCommitOut): void
  (e: 'refresh'): void
  (e: 'access-denied', cause: ApiError): void
}>()
const detail = ref<SchemaFinanceRecordOut | null>(null)
const documents = ref<InstanceType<typeof FinanceRecordDocuments> | null>(null)
const loading = ref(false)
let active = true,
  loadSequence = 0
const {
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
} = useFinanceRecordEditor(props, detail, { saved: (value) => emit('saved', value), denied })
const recordTitle = computed(() =>
  props.recordRef
    ? text(detail.value?.record.merchant) || 'Transaction details'
    : 'New expense or income',
)
const facts = computed(() => [
  {
    label: 'Date',
    value: text(detail.value?.record.business_date) || text(detail.value?.record.date) || 'Undated',
  },
  { label: 'Vendor', value: text(detail.value?.record.merchant) || 'Not supplied' },
  {
    label: 'Amount',
    value:
      typeof currentAmount.value.exponent === 'number'
        ? formatMoney(currentAmount.value)
        : 'Amount needs confirmation',
  },
  {
    label: 'Business purpose',
    value: text(detail.value?.record.business_purpose) || 'Not supplied',
  },
])
function close(value: boolean) {
  if (saving.value || uncertain.value) return
  cancelEdit()
  documents.value?.clear()
  emit('update:modelValue', value)
}
let inspectedScope = ''
let hadAuthority = props.authorityCurrent !== false
function synchronizeScope() {
  const authority = props.authorityCurrent !== false
  const regained = authority && !hadAuthority
  if (!authority && hadAuthority) {
    loadSequence++
    loading.value = false
    suspend()
  }
  hadAuthority = authority
  const scope = JSON.stringify([
    props.modelValue,
    props.projectId,
    props.access.workspace_ref,
    props.access.generation,
    props.recordRef,
    props.snapshot.revision,
    props.snapshot.sha256,
  ])
  if (scope === inspectedScope) {
    if (regained && props.modelValue && props.recordRef && !detail.value && !editing.value)
      void inspectRecord(props.recordRef)
    return
  }
  inspectedScope = scope
  loadSequence++
  cancelEdit()
  documents.value?.clear()
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
  if (
    !active ||
    !props.modelValue ||
    props.authorityCurrent === false ||
    saving.value ||
    uncertain.value
  )
    return
  const origin = inspectionEvent?.currentTarget
  if (origin instanceof HTMLElement && document.activeElement === origin) {
    // Preserve keyboard position before replacing the focused history control.
    origin.closest('[role="dialog"]')?.querySelector<HTMLElement>('[data-autofocus]')?.focus()
  }
  const sequence = ++loadSequence
  cancelEdit()
  documents.value?.clear()
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
  } catch (cause) {
    if (active && sequence === loadSequence) {
      denied(cause)
      error.value =
        'Record unavailable or source changed. Refresh project Finance data before editing.'
    }
  } finally {
    if (active && sequence === loadSequence) loading.value = false
  }
}

function denied(cause: unknown) {
  if (
    cause instanceof ApiError &&
    ([401, 403, 404].includes(cause.status) || text(object(cause.data).finance_code))
  )
    emit('access-denied', cause)
}

onBeforeUnmount(() => {
  active = false
  loadSequence++
  detail.value = null
})
</script>

<template>
  <UiSidePanel
    :model-value="modelValue"
    :title="recordTitle"
    size="xl"
    :hide-close="saving || uncertain"
    :static-backdrop="saving || uncertain"
    @update:model-value="close"
  >
    <p
      v-if="loading || authorityCurrent === false"
      class="text-sm text-fg-muted"
      role="status"
    >
      {{ loading ? 'Loading transaction…' : 'Checking that this transaction is up to date…' }}
    </p>
    <UiCallout
      v-if="error"
      variant="danger"
      role="alert"
    >
      {{ error }}
    </UiCallout>
    <UiCallout
      v-if="notice"
      variant="success"
      role="status"
    >
      {{ notice }}
    </UiCallout>
    <FinanceRecordForm
      v-if="editing"
      v-model="draft"
      :record-ref="recordRef"
      :allowed="allowed"
      :authority-current="authorityCurrent"
      :can-edit="canEdit"
      :saving="saving"
      :uncertain="uncertain"
      :previewing="previewing"
      :stale="stale"
      :preview="preview"
      @change="invalidateDraft"
      @preview="requestPreview"
      @save="save"
      @cancel="cancelEdit"
      @refresh="emit('refresh')"
    />
    <template v-if="detail">
      <div
        v-if="!editing"
        data-test="transaction-facts"
      >
        <UiDescriptionList :items="facts" />
      </div>
      <div
        v-if="canEdit && !editing"
        class="mt-4 flex justify-end"
      >
        <UiButton
          data-test="edit-record"
          variant="primary"
          :disabled="authorityCurrent === false"
          @click="beginEdit"
        >
          Edit transaction
        </UiButton>
      </div>
      <FinanceRecordDocuments
        ref="documents"
        :detail="detail"
        :scope="props"
        :open="modelValue"
        @refresh="emit('refresh')"
        @access-denied="denied"
      />
      <FinanceRecordHistory
        :detail="detail"
        @inspect="inspectRecord"
      />
      <p
        v-if="!canEdit"
        class="mt-3 text-sm text-fg-muted"
      >
        This record is read-only here. Ask your finance agent if it needs a correction.
      </p>
    </template>
  </UiSidePanel>
</template>
