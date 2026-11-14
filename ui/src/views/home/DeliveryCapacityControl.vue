<script setup lang="ts">
import { computed, onBeforeUnmount, onBeforeUpdate, ref, useId } from 'vue'
import type { SchemaDeliveryCapacityOut, SchemaProjectDeliveryCapacityOut, SchemaWriteEnvelopeDeliveryCapacityOut } from '@/api'
import { UiButton, UiIcon, UiIconButton, UiInput, UiPopover } from '@/components/ui'
import { usePolling } from '@/composables/usePolling'
import { formatApiError } from '@/lib/client'
import { callOperation } from '@/lib/operations'
import { useToastsStore } from '@/stores/toasts'

const props = defineProps<{ projectId?: number }>()
const saveErrorId = `task-queues-error-${useId()}`
const toasts = useToastsStore()
const capacity = ref<SchemaDeliveryCapacityOut | null>(null)
const open = ref(false)
const drafts = ref<Record<number, string | number | null>>({})
const saving = ref(false)
const readError = ref<string | null>(null)
const saveError = ref<string | null>(null)
const edited = new Set<number>()
let scope = props.projectId
let revision = 0
let disposed = false
const rows = computed(() => capacity.value?.projects ?? [])
const changed = computed(() => rows.value.filter(row => drafts.value[row.project_id] !== row.configured_limit))
function valid(row: SchemaProjectDeliveryCapacityOut): boolean {
  const value = drafts.value[row.project_id]
  return !!capacity.value && typeof value === 'number' && Number.isInteger(value)
    && value >= capacity.value.supported_min && value <= capacity.value.supported_max
}
const belowRunning = computed(() => !readError.value && rows.value.some(row =>
  (valid(row) ? Number(drafts.value[row.project_id]) : row.configured_limit) < row.running_count))
function count(value: number): string {
  return !readError.value && Number.isInteger(value) && value >= 0 ? String(value) : '—'
}

function accept(result: SchemaDeliveryCapacityOut, resetDrafts = false): void {
  if (result.scope !== 'project' || !Array.isArray(result.projects)) throw new Error('Task queues returned an invalid response.')
  const projects = scope === undefined ? result.projects : result.projects.filter(row => row.project_id === scope)
  if (scope !== undefined && projects.length !== 1) throw new Error('The selected project queue is unavailable.')
  capacity.value = { ...result, projects }
  readError.value = null
  if (resetDrafts) { drafts.value = {}; edited.clear() }
  for (const row of projects) {
    if (!edited.has(row.project_id)) drafts.value[row.project_id] = row.configured_limit
  }
}
async function load(): Promise<void> {
  if (saving.value) return
  const current = revision
  try {
    const result = await callOperation<SchemaDeliveryCapacityOut>('tracker.deliveryCapacity',
      scope === undefined ? {} : { project_id: scope })
    if (!disposed && current === revision) accept(result)
  } catch (cause) {
    if (!disposed && current === revision) readError.value = formatApiError(cause, 'Task queues could not be loaded.')
  }
}
const { refresh, running } = usePolling(load)
// Callers key by project. Also fence direct prop changes and late reads/saves
// before rendering another scope, without a watcher or a second capacity store.
onBeforeUpdate(() => {
  if (scope === props.projectId) return
  scope = props.projectId
  revision++
  capacity.value = null
  drafts.value = {}
  edited.clear()
  open.value = false
  saving.value = false
  readError.value = null
  saveError.value = null
  void load()
})
onBeforeUnmount(() => { disposed = true; revision++ })
defineExpose({ refresh })

function setOpen(next: boolean): void {
  if (saving.value) return
  open.value = next
  if (next) {
    edited.clear()
    drafts.value = Object.fromEntries(rows.value.map(row => [row.project_id, row.configured_limit]))
    saveError.value = null
    void refresh()
  }
}
function edit(id: number, next: string | number | null): void {
  drafts.value[id] = next
  edited.add(id)
  saveError.value = null
}
function step(row: SchemaProjectDeliveryCapacityOut, delta: number): void {
  if (!capacity.value) return
  edit(row.project_id, Math.min(capacity.value.supported_max, Math.max(capacity.value.supported_min,
    (valid(row) ? Number(drafts.value[row.project_id]) : row.configured_limit) + delta)))
}
async function save(): Promise<void> {
  if (saving.value || !capacity.value || !changed.value.length) return
  if (rows.value.some(row => !valid(row))) {
    saveError.value = `Enter a whole number from ${capacity.value.supported_min} to ${capacity.value.supported_max} for each project.`
    return
  }
  const updates = changed.value.map(row => ({ project_id: row.project_id, max_concurrent_tasks: Number(drafts.value[row.project_id]) }))
  saving.value = true
  saveError.value = null
  const current = ++revision // A pre-save read cannot overwrite the durable readback.
  try {
    const result = await callOperation<SchemaWriteEnvelopeDeliveryCapacityOut>('tracker.setDeliveryCapacity', { updates })
    if (disposed || current !== revision) return
    accept(result.data, true)
    open.value = false
    toasts.success('Task queues saved')
  } catch (cause) {
    if (!disposed && current === revision) saveError.value = `Save could not be confirmed. ${formatApiError(cause)} Your draft is still here.`
  } finally {
    if (!disposed && current === revision) saving.value = false
  }
}
</script>

<template>
  <UiPopover
    :model-value="open"
    placement="bottom-end"
    :width="544"
    aria-label="Task queues"
    :close-on-esc="!saving"
    :close-on-click-outside="!saving"
    @update:model-value="setOpen"
  >
    <template #trigger>
      <UiButton
        data-popover-trigger
        size="sm"
        variant="secondary"
        icon-left="library"
        icon-right="chevron-down"
        aria-haspopup="dialog"
        :aria-expanded="open"
        :aria-label="readError ? 'Task queues: counts unavailable' : 'Task queues'"
        :disabled="saving"
        class="whitespace-nowrap"
        @click="setOpen(!open)"
      >
        Task queues
      </UiButton>
    </template>
    <div class="queue-body space-y-3 p-3">
      <div>
        <h2 class="t-h3">
          Task queues
        </h2>
        <p class="mt-1 text-xs text-fg-muted">
          Set how many tasks each project can run.
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
          {{ readError }} Counts unavailable.<template v-if="capacity">
            Showing last confirmed limits.
          </template>
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
        class="space-y-3"
        novalidate
        @submit.prevent="save"
      >
        <div class="queue-table-scroll">
          <table class="queue-table w-full table-fixed text-sm">
            <colgroup>
              <col>
              <col class="queue-count-column">
              <col class="queue-count-column">
              <col class="queue-limit-column">
            </colgroup>
            <thead class="text-xs text-fg-muted">
              <tr>
                <th
                  scope="col"
                  class="text-left font-medium"
                >
                  Project
                </th>
                <th
                  scope="col"
                  class="text-center font-medium"
                >
                  Running
                </th>
                <th
                  scope="col"
                  class="text-center font-medium"
                >
                  Waiting
                </th>
                <th
                  scope="col"
                  class="text-center font-medium"
                >
                  Run at once
                </th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="row in rows"
                :key="row.project_id"
                class="border-b border-subtle"
              >
                <th
                  scope="row"
                  class="text-left font-normal"
                >
                  <div class="flex min-w-0 items-center gap-2 pr-2">
                    <span
                      class="flex h-6 w-6 shrink-0 items-center justify-center rounded-xs bg-accent-subtle text-xs font-semibold text-fg-default"
                      aria-hidden="true"
                    >{{ row.project_name.charAt(0).toUpperCase() }}</span>
                    <span
                      class="truncate"
                      :title="row.project_name"
                    >{{ row.project_name }}</span>
                  </div>
                </th>
                <td
                  data-count="running"
                  class="text-center tabular-nums"
                >
                  {{ count(row.running_count) }}
                </td>
                <td
                  data-count="waiting"
                  class="text-center tabular-nums"
                >
                  {{ count(row.queued_count) }}
                </td>
                <td>
                  <div class="flex items-center justify-between gap-1">
                    <UiIconButton
                      :aria-label="`Decrease run at once for ${row.project_name}`"
                      class="shrink-0"
                      size="sm"
                      variant="secondary"
                      :disabled="saving || (valid(row) && Number(drafts[row.project_id]) <= capacity.supported_min)"
                      @click="step(row, -1)"
                    >
                      <UiIcon name="minus" />
                    </UiIconButton>
                    <div class="queue-limit-input">
                      <label
                        :for="`${saveErrorId}-${row.project_id}`"
                        class="sr-only"
                      >Run at once for {{ row.project_name }}</label>
                      <UiInput
                        :id="`${saveErrorId}-${row.project_id}`"
                        :model-value="drafts[row.project_id]"
                        :aria-label="`Run at once for ${row.project_name}`"
                        :aria-describedby="saveError ? saveErrorId : undefined"
                        :title="`Saved: ${row.configured_limit}. Whole number from ${capacity.supported_min} to ${capacity.supported_max}.`"
                        size="sm"
                        type="number"
                        :min="capacity.supported_min"
                        :max="capacity.supported_max"
                        :step="1"
                        :disabled="saving"
                        :invalid="!!saveError && !valid(row)"
                        @update:model-value="edit(row.project_id, $event)"
                      />
                    </div>
                    <UiIconButton
                      :aria-label="`Increase run at once for ${row.project_name}`"
                      class="shrink-0"
                      size="sm"
                      variant="secondary"
                      :disabled="saving || (valid(row) && Number(drafts[row.project_id]) >= capacity.supported_max)"
                      @click="step(row, 1)"
                    >
                      <UiIcon name="plus" />
                    </UiIconButton>
                  </div>
                  <p
                    v-if="saveError"
                    class="mt-1 text-center text-2xs text-fg-muted"
                  >
                    Saved: {{ row.configured_limit }}
                  </p>
                </td>
              </tr>
            </tbody>
          </table>
          <p
            v-if="!rows.length"
            role="status"
            class="py-3 text-xs text-fg-muted"
          >
            No projects yet.
          </p>
        </div>
        <p
          v-if="belowRunning"
          class="text-xs text-fg-muted"
        >
          Running tasks continue. New tasks wait until running work falls below the saved limit.
        </p>
        <p
          v-if="saveError"
          :id="saveErrorId"
          role="alert"
          class="text-xs text-danger-fg"
        >
          {{ saveError }}
        </p>
        <div class="flex flex-wrap items-center justify-end gap-2">
          <p class="mr-auto text-xs text-fg-muted">
            Only Todo tasks enter the queue.
          </p>
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
            :disabled="!changed.length"
          >
            Save changes
          </UiButton>
        </div>
      </form>
      <p
        v-else-if="!readError"
        class="text-xs text-fg-muted"
        role="status"
      >
        Loading task queues…
      </p>
    </div>
  </UiPopover>
</template>

<style scoped>
.queue-body { max-height: calc(100dvh - 120px); overflow-y: auto; }
.queue-table-scroll { max-height: min(420px, calc(100dvh - 280px)); overflow-y: auto; }
.queue-table th, .queue-table td { padding-top: 8px; padding-bottom: 8px; }
.queue-table thead { position: sticky; top: 0; background: var(--color-bg-surface); z-index: 1; }
.queue-table thead tr { border-bottom: 1px solid var(--color-border-default); }
.queue-count-column { width: 68px; }
.queue-limit-column { width: 128px; }
.queue-limit-input { flex: 1; width: 0; min-width: 0; }
.queue-limit-input :deep(.ui-input) { padding-left: 4px; padding-right: 4px; }
.queue-limit-input :deep(input) { width: 100%; text-align: center; appearance: textfield; }
.queue-limit-input :deep(input::-webkit-inner-spin-button),
.queue-limit-input :deep(input::-webkit-outer-spin-button) { appearance: none; margin: 0; }
@media (max-width: 520px) {
  .queue-count-column { width: 52px; }
  .queue-limit-column { width: 104px; }
}
</style>
