<script setup lang="ts">
import { computed, ref } from 'vue'

import type { SchemaActionCallAuditOut } from '@/api'
import DataTable from '@/components/DataTable.vue'
import InspectableDetailDrawer from '@/components/InspectableDetailDrawer.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import {
  UiAdvancedJsonPanel,
  UiBadge,
  UiButton,
  UiCallout,
  UiFactGroups,
  UiIcon,
  UiMetadataStrip,
  UiProgressBar,
  UiSectionHeader,
} from '@/components/ui'
import type { DataTableColumn } from '@/components/types'
import type { UiFactGroup } from '@/components/ui/UiFactGroups.vue'
import type { UiMetadataStripItem } from '@/components/ui/UiMetadataStrip.vue'
import { formatDateTime, sanitizeForDisplay } from '@/lib/stackos/json'

import type {
  DurableActionCallItems,
  DurableActionControl,
  DurableActionControlRequest,
  DurableActionItem,
} from './useDurableActionCall'

const props = defineProps<{
  modelValue: boolean
  call: SchemaActionCallAuditOut | null
  durable: DurableActionCallItems | null
  durableLoading: boolean
  durableError: string | null
  controlLoading: DurableActionControl | null
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'durable-control', value: DurableActionControlRequest): void
}>()

const title = computed(() => (props.call ? `Action call #${props.call.id}` : 'Action call'))
const description = computed(() => (props.call ? callTitle(props.call) : undefined))

const executionContext = computed(() => recordValue(props.call?.metadata_json?.execution_context))
const fileBackedOutput = computed(() => recordValue(props.call?.metadata_json?.file_backed_output))

const durableJob = computed(() => props.durable?.job ?? null)
const durableSettledCount = computed(() => {
  const job = durableJob.value
  if (!job) return 0
  return job.completed_count + job.failed_count + job.cancelled_count
})
const durablePartial = computed(() => {
  const job = durableJob.value
  if (!job || job.completed_count === 0) return false
  return job.failed_count > 0 || job.cancelled_count > 0 || job.unknown_count > 0
})
const canPause = computed(() => ['scheduled', 'running'].includes(durableJob.value?.state ?? ''))
const canResume = computed(() => durableJob.value?.state === 'paused')
const canCancel = computed(() => durableJob.value?.can_cancel === true)
const retrySelection = ref<Set<number>>(new Set())
const retryableItems = computed(() => props.durable?.items.filter(isRetryEligible) ?? [])
const retryableItemIds = computed(() => new Set(retryableItems.value.map((item) => item.id)))
const retrySelectionForCurrentJob = computed(
  () =>
    new Set(
      [...retrySelection.value].filter((itemId) => retryableItemIds.value.has(itemId)),
    ),
)
const selectedRetryItemIds = computed(() => [...retrySelectionForCurrentJob.value])
const canRetry = computed(() => selectedRetryItemIds.value.length > 0)

const durableFacts = computed<UiFactGroup[]>(() => {
  const job = durableJob.value
  if (!job) return []
  return [
    {
      title: 'Dispatch state',
      description: 'Sealed items stay paced and auditable until each receipt is terminal or held unknown.',
      items: [
        { label: 'Scheduled for', value: formatDateTime(job.due_at), wide: true },
        {
          label: 'Expires at',
          value: job.expires_at ? formatDateTime(job.expires_at) : null,
          wide: true,
        },
        {
          label: 'Account pace',
          value: formatInterval(job.pacing_json.account_interval_seconds),
        },
        {
          label: 'Destination pace',
          value: formatInterval(job.pacing_json.destination_interval_seconds),
        },
        {
          label: 'Next eligible',
          value: job.next_eligible_at ? formatDateTime(job.next_eligible_at) : null,
          wide: true,
        },
        { label: 'Queued', value: job.pending_count },
        { label: 'In flight', value: job.leased_count },
        { label: 'Delivered', value: job.completed_count },
        { label: 'Failed', value: job.failed_count },
        { label: 'Cancelled', value: job.cancelled_count },
        { label: 'Unknown receipt', value: job.unknown_count },
        { label: 'Partial outcome', value: durablePartial.value },
      ],
    },
  ]
})

const durableItemColumns: DataTableColumn<DurableActionItem>[] = [
  { key: 'ordinal', label: 'Item', widthClass: 'w-16', format: (value) => `#${Number(value) + 1}` },
  { key: 'destination_ref', label: 'Destination', widthClass: 'w-44' },
  { key: 'state', label: 'Receipt', widthClass: 'w-32' },
  { key: 'attempt_count', label: 'Attempts', widthClass: 'w-20' },
  { key: 'next_eligible_at', label: 'Next eligible', widthClass: 'w-40' },
  {
    key: 'can_retry',
    label: 'Retry',
    widthClass: 'w-24',
    format: (value) => (value === true ? 'Eligible' : 'Unavailable'),
  },
]

function updateRetrySelection(selection: Set<number | string>): void {
  retrySelection.value = new Set(
    [...selection].filter(
      (itemId): itemId is number => typeof itemId === 'number' && retryableItemIds.value.has(itemId),
    ),
  )
}

function requestRetry(): void {
  if (!canRetry.value) return
  emitDurableControl({ action: 'retry', itemIds: selectedRetryItemIds.value })
}

function emitDurableControl(value: DurableActionControlRequest): void {
  emit('durable-control', value)
}

const durableReceiptDetails = computed(() =>
  props.durable?.items.map((item) => ({
    item: item.ordinal + 1,
    destination_ref: item.destination_ref,
    correlation_ref: item.correlation_ref,
    state: item.state,
    attempt_ref: item.attempt_ref,
    provider_sending_id: item.provider_sending_id,
    temporary_message_ref: item.temporary_message_ref,
    final_message_ref: item.final_message_ref,
    error: item.error,
    next_eligible_at: item.next_eligible_at,
    progress_json: item.progress_json,
    result_json: item.result_json,
  })) ?? [],
)

const providerEvidenceGroups = computed<UiFactGroup[]>(() => {
  const call = props.call
  if (!call) return []
  const response = recordValue(call.response_json)
  const metadata = recordValue(call.metadata_json)
  const resultState = stringValue(response?.status)
  const partialFailure =
    booleanValue(metadata?.partial_failure) ?? (resultState === 'partial' ? true : null)
  const retryAfter = scalarValue(
    firstValue([response, metadata], ['retry_after', 'retry_after_seconds']),
  )
  const items: UiFactGroup['items'] = [
    {
      label: 'Provider state',
      value: scalarValue(firstValue([response, metadata], ['provider_status', 'provider_state'])),
    },
    { label: 'Result state', value: resultState },
    {
      label: 'Request ID',
      value: stringValue(firstValue([response, metadata], ['request_id', 'provider_request_id'])),
      mono: true,
      wide: true,
    },
    {
      label: 'Provider status code',
      value: scalarValue(firstValue([response, metadata], ['status_code'])),
    },
    { label: 'Partial result', value: partialFailure },
    {
      label: 'Errors',
      value: scalarValue(
        firstValue([response, metadata], ['error_count', 'failure_count', 'missing_count']),
      ),
    },
    {
      label: 'Result available',
      value: booleanValue(firstValue([response, metadata], ['result_available'])),
    },
    {
      label: 'Response complete',
      value: booleanValue(firstValue([response, metadata], ['response_complete'])),
    },
    {
      label: 'Retryable',
      value: booleanValue(firstValue([response, metadata], ['retryable'])),
    },
    { label: 'Retry after', value: retryAfter === null ? null : `${retryAfter}s` },
    {
      label: 'Next action',
      value: nextActionValue(firstValue([response, metadata], ['next_action'])),
      wide: true,
    },
    {
      label: 'Artifact ref',
      value: stringValue(firstValue([response, metadata], ['artifact_ref'])),
      mono: true,
      wide: true,
    },
    {
      label: 'Filename',
      value: stringValue(firstValue([response, metadata], ['filename'])),
      wide: true,
    },
    {
      label: 'MIME type',
      value: stringValue(firstValue([response, metadata], ['mime_type', 'content_type'])),
      mono: true,
    },
    {
      label: 'Artifact bytes',
      value: scalarValue(firstValue([response, metadata], ['size_bytes'])),
    },
  ].filter((item) => item.value !== null && item.value !== undefined && item.value !== '')

  if (items.length === 0) return []
  return [
    {
      title: 'Provider evidence',
      description: 'Safe provider state, repair context, and result pointers from this call.',
      items,
    },
  ]
})

const headerFacts = computed<UiMetadataStripItem[]>(() => {
  const call = props.call
  if (!call) return []
  return [
    { label: 'Provider', value: call.provider_key ?? null },
    { label: 'Connector', value: call.connector_key ?? null },
    { label: 'Operation', value: call.operation, mono: true, title: call.operation },
    { label: 'Run', value: call.run_id ? `#${call.run_id}` : null },
    { label: 'Run plan', value: call.run_plan_id ? `#${call.run_plan_id}` : null },
    { label: 'Step', value: call.run_plan_step_id ? `#${call.run_plan_step_id}` : null },
    { label: 'Cost', value: `${call.cost_cents} cents` },
    { label: 'Duration', value: formatDuration(call.duration_ms) },
    { label: 'Dry run', value: call.dry_run },
    { label: 'Created', value: formatDateTime(call.created_at) },
    {
      label: 'Completed',
      value: call.completed_at ? formatDateTime(call.completed_at) : null,
    },
  ]
})

// Sensitive / technical detail (credential refs, schema/sha256/path/namespace)
// lives only in the collapsed Advanced disclosure, never in the visible strip.
const advancedGroups = computed<UiFactGroup[]>(() => {
  const call = props.call
  if (!call) return []
  const context = executionContext.value
  const outputPolicy = recordValue(context?.output_policy_json)
  const requestBudget = recordValue(context?.request_budget_json)
  const fileOutput = fileBackedOutput.value
  const hasRunContext = Boolean(call.run_id || call.run_plan_id || call.run_plan_step_id)
  const groups: UiFactGroup[] = [
    {
      title: 'Execution Target',
      description: 'Provider, connector, and operation resolved by StackOS.',
      items: [
        { label: 'Provider', value: call.provider_key ?? null, emphasis: 'strong' },
        { label: 'Connector', value: call.connector_key ?? null },
        { label: 'Operation', value: call.operation, mono: true, wide: true },
        { label: 'Credential', value: call.credential_ref ?? null, mono: true, wide: true },
      ],
    },
    {
      title: 'Run Context',
      description: hasRunContext
        ? 'Workflow/run attachment for this audited call.'
        : 'Direct action call; not attached to a run.',
      items: [
        { label: 'Run', value: call.run_id ? `#${call.run_id}` : null },
        { label: 'Run plan', value: call.run_plan_id ? `#${call.run_plan_id}` : null },
        { label: 'Step', value: call.run_plan_step_id ? `#${call.run_plan_step_id}` : null },
      ],
    },
    {
      title: 'Outcome',
      description: call.dry_run
        ? 'Validation-only execution.'
        : 'Recorded provider execution result.',
      items: [
        { label: 'Cost', value: `${call.cost_cents} cents` },
        { label: 'Duration', value: formatDuration(call.duration_ms) },
        {
          label: 'Dry run',
          value: call.dry_run,
          badge: true,
          tone: call.dry_run ? 'warning' : 'neutral',
        },
      ],
    },
    {
      title: 'Timeline',
      items: [
        { label: 'Created', value: formatDateTime(call.created_at), wide: true },
        {
          label: 'Completed',
          value: call.completed_at ? formatDateTime(call.completed_at) : null,
          wide: true,
        },
      ],
    },
  ]
  if (context) {
    groups.splice(2, 0, {
      title: 'Execution Context',
      description: 'Reusable provider defaults applied by StackOS for this call.',
      items: [
        { label: 'Context ref', value: stringValue(context.context_ref), mono: true, wide: true },
        { label: 'Output mode', value: stringValue(outputPolicy?.mode) },
        { label: 'Max parallel', value: numberValue(requestBudget?.max_parallel) },
        { label: 'Max calls', value: numberValue(requestBudget?.max_calls) },
        {
          label: 'Artifact namespace',
          value: stringValue(context.artifact_namespace),
          mono: true,
          wide: true,
        },
      ],
    })
  }
  if (fileOutput) {
    groups.splice(context ? 3 : 2, 0, {
      title: 'File Output',
      description: 'Sanitized request and response stored in a response file.',
      items: [
        {
          label: 'Schema version',
          value: stringValue(fileOutput.schema_version),
          mono: true,
          wide: true,
        },
        { label: 'Schema ref', value: stringValue(fileOutput.schema_ref), mono: true, wide: true },
        {
          label: 'Schema operation',
          value: stringValue(fileOutput.schema_operation),
          mono: true,
          wide: true,
        },
        { label: 'Content type', value: stringValue(fileOutput.content_type), mono: true },
        { label: 'Bytes', value: numberValue(fileOutput.bytes) },
        { label: 'SHA-256', value: stringValue(fileOutput.sha256), mono: true, wide: true },
        { label: 'Path', value: stringValue(fileOutput.path), mono: true, wide: true },
      ],
    })
  }
  return groups
})

function callTitle(call: SchemaActionCallAuditOut): string {
  return `${call.plugin_slug}:${call.action_key}`
}

function formatDuration(value: number | null | undefined): string {
  return value === null || value === undefined ? '-' : `${value}ms`
}

function formatInterval(seconds: number | undefined): string {
  if (seconds === undefined || !Number.isFinite(seconds)) return '—'
  return `${seconds}s`
}

function durableStateTone(state: string): 'neutral' | 'info' | 'success' | 'warning' | 'danger' {
  if (state === 'completed') return 'success'
  if (state === 'failed') return 'danger'
  if (state === 'unknown-hold' || state === 'paused' || state === 'cancelled') return 'warning'
  if (state === 'running' || state === 'leased') return 'info'
  return 'neutral'
}

function receiptSummary(item: DurableActionItem): string {
  return item.final_message_ref ?? item.temporary_message_ref ?? item.error ?? 'Pending receipt'
}

function isRetryEligible(item: DurableActionItem): boolean {
  return item.can_retry
}

function recordValue(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  return value as Record<string, unknown>
}

function stringValue(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null
}

function numberValue(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function scalarValue(value: unknown): string | number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  return stringValue(value)
}

function booleanValue(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null
}

function firstValue(records: Array<Record<string, unknown> | null>, keys: string[]): unknown {
  for (const record of records) {
    if (!record) continue
    for (const key of keys) {
      if (record[key] !== null && record[key] !== undefined && record[key] !== '') {
        return record[key]
      }
    }
  }
  return null
}

function nextActionValue(value: unknown): string | null {
  const direct = stringValue(value)
  if (direct) return direct
  const action = recordValue(value)
  if (!action) return null
  return (
    stringValue(action.label) ??
    stringValue(action.summary) ??
    stringValue(action.hint) ??
    stringValue(action.kind)
  )
}
</script>

<template>
  <InspectableDetailDrawer
    :model-value="modelValue"
    :title="title"
    :description="description"
    size="lg"
    :has-detail="Boolean(call)"
    empty-title="No action call selected"
    empty-description="Select an audit row to inspect its error, facts, and redacted payloads."
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <template #header="{ titleId, descriptionId }">
      <div
        v-if="call"
        class="min-w-0"
      >
        <div class="flex flex-wrap items-center gap-2">
          <h2
            :id="titleId"
            class="t-h2 text-fg-strong"
          >
            Action call #{{ call.id }}
          </h2>
          <StatusBadge
            :status="call.status"
            kind="job"
            :small="true"
          />
        </div>
        <p
          :id="descriptionId"
          class="mt-1 truncate font-mono text-xs text-fg-muted"
        >
          {{ callTitle(call) }}
        </p>
      </div>
    </template>

    <div
      v-if="call"
      class="grid gap-4"
    >
      <UiCallout
        v-if="call.error"
        tone="danger"
        density="compact"
      >
        {{ call.error }}
      </UiCallout>

      <UiMetadataStrip
        :items="headerFacts"
        aria-label="Action call facts"
      />

      <UiFactGroups
        v-if="providerEvidenceGroups.length > 0"
        :groups="providerEvidenceGroups"
        density="compact"
        aria-label="Provider action evidence"
      />

      <section
        v-if="durableLoading || durable || durableError"
        class="min-w-0"
        aria-label="Durable delivery lifecycle"
      >
        <UiSectionHeader
          title="Durable delivery"
          description="Progress, receipt recovery, schedule, and permitted controls for this Action Call."
          as="h3"
        >
          <template #actions>
            <div
              v-if="durable"
              class="flex flex-wrap items-center gap-2"
            >
              <UiBadge
                :tone="durableStateTone(durable.job.state)"
                variant="subtle"
              >
                {{ durable.job.state }}
              </UiBadge>
              <UiButton
                v-if="canPause"
                size="sm"
                variant="secondary"
                :loading="controlLoading === 'pause'"
                :disabled="controlLoading !== null"
                @click="emitDurableControl({ action: 'pause' })"
              >
                Pause
              </UiButton>
              <UiButton
                v-if="canResume"
                size="sm"
                variant="secondary"
                :loading="controlLoading === 'resume'"
                :disabled="controlLoading !== null"
                @click="emitDurableControl({ action: 'resume' })"
              >
                Resume
              </UiButton>
              <UiButton
                v-if="canCancel"
                size="sm"
                variant="danger-ghost"
                :loading="controlLoading === 'cancel'"
                :disabled="controlLoading !== null"
                @click="emitDurableControl({ action: 'cancel' })"
              >
                Cancel delivery
              </UiButton>
            </div>
          </template>
        </UiSectionHeader>

        <UiCallout
          v-if="durableError"
          tone="danger"
          density="compact"
          class="mb-3"
        >
          {{ durableError }}
        </UiCallout>

        <div
          v-else-if="durableLoading"
          class="rounded-lg border border-subtle bg-bg-surface-alt p-3"
        >
          <UiProgressBar
            :value="null"
            aria-label="Loading durable delivery lifecycle"
          />
        </div>

        <template v-else-if="durable">
          <div class="rounded-lg border border-subtle bg-bg-surface-alt p-3">
            <div class="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs text-fg-muted">
              <span>{{ durableSettledCount }} / {{ durable.job.item_count }} settled</span>
              <span>{{ durable.count }} sealed item{{ durable.count === 1 ? '' : 's' }}</span>
            </div>
            <UiProgressBar
              :value="durableSettledCount"
              :max="Math.max(durable.job.item_count, 1)"
              :tone="
                durable.job.unknown_count > 0 || durable.job.failed_count > 0
                  ? 'danger'
                  : durable.job.cancelled_count > 0
                    ? 'warning'
                    : 'success'
              "
              :show-label="false"
              aria-label="Durable delivery progress"
            />
          </div>

          <UiCallout
            v-if="durable.job.unknown_count > 0"
            tone="warning"
            density="compact"
            class="mt-3"
          >
            Unknown receipt: {{ durable.job.unknown_count }} item{{ durable.job.unknown_count === 1 ? '' : 's' }} will not replay automatically while StackOS waits for an exact provider receipt.
          </UiCallout>

          <UiFactGroups
            class="mt-3"
            :groups="durableFacts"
            density="compact"
            aria-label="Durable delivery facts"
          />

          <div class="mt-3">
            <UiSectionHeader
              title="Item receipts"
              description="Each destination is sealed before dispatch; select only a known failed item with no acceptance evidence to retry it."
              as="h4"
            >
              <template #actions>
                <UiButton
                  v-if="retryableItems.length > 0"
                  size="sm"
                  variant="secondary"
                  :loading="controlLoading === 'retry'"
                  :disabled="controlLoading !== null || !canRetry"
                  @click="requestRetry"
                >
                  Retry selected
                </UiButton>
              </template>
            </UiSectionHeader>
            <p
              v-if="retryableItems.length > 0"
              class="mb-2 text-xs text-fg-muted"
            >
              {{ retryableItems.length }} item{{ retryableItems.length === 1 ? '' : 's' }} can be retried safely. Completed, in-flight, unknown, and receipt-backed items remain unavailable.
            </p>
            <DataTable
              :key="`durable-items-${durable.job.id}`"
              :items="durable.items"
              :columns="durableItemColumns"
              :selection="retrySelectionForCurrentJob"
              aria-label="Durable delivery item receipts"
              empty-message="No sealed item receipts are available."
              max-height="16rem"
              @selection-change="updateRetrySelection"
            >
              <template #cell:state="{ row }">
                <div class="min-w-0">
                  <UiBadge
                    :tone="durableStateTone(row.state)"
                    variant="subtle"
                  >
                    {{ row.state }}
                  </UiBadge>
                  <p class="mt-1 truncate font-mono text-2xs text-fg-muted">
                    {{ receiptSummary(row) }}
                  </p>
                </div>
              </template>
              <template #cell:next_eligible_at="{ row }">
                <span class="text-xs text-fg-muted">{{ formatDateTime(row.next_eligible_at) }}</span>
              </template>
            </DataTable>
          </div>

          <UiAdvancedJsonPanel
            class="mt-3"
            title="Item receipt details"
            summary="Sanitized sealed targets, progress, and provider receipt evidence"
            :data="sanitizeForDisplay(durableReceiptDetails)"
          />
        </template>
      </section>

      <details class="group rounded-lg border border-subtle bg-bg-sunken">
        <summary
          class="focus-ring flex cursor-pointer list-none items-center gap-1.5 rounded-lg px-3 py-1.5 text-2xs font-medium text-fg-muted transition-colors duration-fast hover:text-fg-default [&::-webkit-details-marker]:hidden"
        >
          <UiIcon
            name="chevron-right"
            class="h-3 w-3 text-fg-subtle transition-transform duration-fast group-open:rotate-90"
            aria-hidden="true"
          />
          Advanced
          <span class="font-normal text-fg-subtle">Detail facts, schema &amp; file output</span>
        </summary>
        <div class="border-t border-subtle p-3">
          <UiFactGroups
            :groups="advancedGroups"
            density="compact"
            aria-label="Action call detail facts"
          />
        </div>
      </details>

      <UiAdvancedJsonPanel
        title="Request"
        summary="Sanitized request payload"
        :data="sanitizeForDisplay(call.request_json ?? {})"
      />
      <UiAdvancedJsonPanel
        title="Response"
        summary="Sanitized response payload"
        :data="sanitizeForDisplay(call.response_json ?? {})"
      />
      <UiAdvancedJsonPanel
        title="Provider context"
        summary="Acting-as and provider context"
        :data="sanitizeForDisplay(call.provider_context_json ?? {})"
      />
      <UiAdvancedJsonPanel
        title="Metadata"
        summary="Execution metadata"
        :data="sanitizeForDisplay(call.metadata_json ?? {})"
      />
    </div>
  </InspectableDetailDrawer>
</template>
