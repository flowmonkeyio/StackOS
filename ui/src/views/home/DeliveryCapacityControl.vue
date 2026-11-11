<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import type { SchemaDeliveryCapacityOut, SchemaWriteEnvelopeDeliveryCapacityOut } from '@/api'
import { UiButton, UiCallout, UiFormField, UiIcon, UiIconButton, UiInput, UiPopover } from '@/components/ui'
import { usePolling } from '@/composables/usePolling'
import { formatApiError } from '@/lib/client'
import { callOperation } from '@/lib/operations'

const capacity = ref<SchemaDeliveryCapacityOut | null>(null)
const open = ref(false)
const draft = ref<string | number | null>(null)
const saving = ref(false)
const readError = ref<string | null>(null)
const saveError = ref<string | null>(null)
let edited = false
let revision = 0
let disposed = false
const value = computed(() => typeof draft.value === 'number' ? draft.value : NaN)
const valid = computed(() => capacity.value !== null && Number.isInteger(value.value)
  && value.value >= capacity.value.supported_min && value.value <= capacity.value.supported_max)
const belowRunning = computed(() => capacity.value !== null
  && (valid.value ? value.value : capacity.value.configured_limit) < capacity.value.running_count)

async function load(): Promise<void> {
  if (saving.value) return
  const current = revision
  try {
    const result = await callOperation<SchemaDeliveryCapacityOut>('tracker.deliveryCapacity', {})
    if (disposed || current !== revision) return
    capacity.value = result
    readError.value = null
    if (open.value && !edited) draft.value = result.configured_limit
  } catch (cause) {
    if (!disposed && current === revision) readError.value = formatApiError(cause, 'Capacity could not be loaded.')
  }
}
const { refresh, running } = usePolling(load)
onBeforeUnmount(() => { disposed = true; revision++ })
defineExpose({ refresh })

function setOpen(next: boolean): void {
  if (saving.value) return
  open.value = next
  if (next) {
    edited = false
    draft.value = capacity.value?.configured_limit ?? null
    saveError.value = null
    void refresh()
  }
}
function edit(next: string | number | null): void {
  draft.value = next
  edited = true
  saveError.value = null
}
function step(delta: number): void {
  if (!capacity.value) return
  edit(Math.min(capacity.value.supported_max, Math.max(capacity.value.supported_min,
    (valid.value ? value.value : capacity.value.configured_limit) + delta)))
}
async function save(): Promise<void> {
  if (saving.value || !capacity.value) return
  if (!valid.value) {
    saveError.value = `Enter a whole number from ${capacity.value.supported_min} to ${capacity.value.supported_max}.`
    return
  }
  saving.value = true
  saveError.value = null
  revision++ // A read started before this save cannot overwrite its readback.
  try {
    const result = await callOperation<SchemaWriteEnvelopeDeliveryCapacityOut>('tracker.setDeliveryCapacity', { concurrent_tasks: value.value })
    if (disposed) return
    // The operation returns effective counts/limit after its durable save.
    capacity.value = result.data
    readError.value = null
    open.value = false
  } catch (cause) {
    if (!disposed) saveError.value = `Save could not be confirmed. ${formatApiError(cause)} Your draft is still here.`
  } finally {
    if (!disposed) saving.value = false
  }
}
</script>

<template>
  <UiPopover
    :model-value="open"
    placement="bottom-end"
    :width="320"
    aria-label="Concurrent tasks"
    :close-on-esc="!saving"
    :close-on-click-outside="!saving"
    @update:model-value="setOpen"
  >
    <template #trigger>
      <UiButton
        data-popover-trigger
        size="sm"
        variant="secondary"
        icon-right="chevron-down"
        aria-haspopup="dialog"
        :aria-expanded="open"
        :aria-label="readError ? 'Concurrent tasks: capacity unavailable' : capacity ? `Concurrent tasks: ${capacity.running_count} running of ${capacity.configured_limit}` : 'Loading task capacity'"
        :disabled="saving"
        class="tabular-nums whitespace-nowrap"
        @click="setOpen(!open)"
      >
        {{ readError ? 'Capacity unavailable' : capacity ? `${capacity.running_count} of ${capacity.configured_limit} running` : 'Loading capacity…' }}
      </UiButton>
    </template>
    <div class="space-y-4 p-3">
      <div>
        <h2 class="t-h3">
          Concurrent tasks
        </h2>
        <p class="mt-1 text-xs text-fg-muted">
          Shared across all projects in this local user's daemon.
        </p>
      </div>
      <div
        v-if="readError"
        class="space-y-2"
      >
        <p
          role="alert"
          class="text-xs text-danger-fg"
        >
          {{ readError }}
        </p>
        <UiButton
          size="sm"
          :loading="running"
          @click="refresh"
        >
          Retry read
        </UiButton>
      </div>
      <form
        v-if="capacity"
        class="space-y-4"
        novalidate
        @submit.prevent="save"
      >
        <p class="text-xs text-fg-muted tabular-nums">
          {{ readError ? 'Last confirmed:' : 'Effective:' }} {{ capacity.configured_limit }} concurrent · {{ capacity.running_count }} running · {{ capacity.queued_count }} queued
        </p>
        <UiFormField
          label="Concurrent task limit"
          :error="saveError"
          :help="`Whole number from ${capacity.supported_min} to ${capacity.supported_max}.`"
          :dirty="draft !== capacity.configured_limit"
        >
          <div class="flex items-center gap-2">
            <UiIconButton
              aria-label="Decrease concurrent tasks"
              variant="secondary"
              :disabled="saving || (valid && value <= capacity.supported_min)"
              @click="step(-1)"
            >
              <UiIcon name="minus" />
            </UiIconButton>
            <UiInput
              :model-value="draft"
              type="number"
              :min="capacity.supported_min"
              :max="capacity.supported_max"
              :step="1"
              :disabled="saving"
              @update:model-value="edit"
            />
            <UiIconButton
              aria-label="Increase concurrent tasks"
              variant="secondary"
              :disabled="saving || (valid && value >= capacity.supported_max)"
              @click="step(1)"
            >
              <UiIcon name="plus" />
            </UiIconButton>
          </div>
        </UiFormField>
        <UiCallout
          v-if="belowRunning"
          tone="warning"
        >
          Running tasks continue. New tasks wait until running work falls below the saved limit.
        </UiCallout>
        <div class="flex justify-end gap-2">
          <UiButton
            variant="ghost"
            :disabled="saving"
            @click="setOpen(false)"
          >
            Cancel
          </UiButton>
          <UiButton
            type="submit"
            variant="primary"
            :loading="saving"
          >
            Save
          </UiButton>
        </div>
      </form>
      <p
        v-else-if="!readError"
        class="text-xs text-fg-muted"
        role="status"
      >
        Loading capacity…
      </p>
    </div>
  </UiPopover>
</template>
