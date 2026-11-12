<script setup lang="ts">
import { computed, onBeforeUnmount, provide, ref } from 'vue'
import { storeToRefs } from 'pinia'
import DataTable from '@/components/DataTable.vue'
import TabBar from '@/components/TabBar.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { UiButton, UiCallout, UiConfirmDialog, UiDropdownMenu, UiIcon, UiIconButton, UiSwitch } from '@/components/ui'
import { useProjectRouteScope } from '@/composables/useProjectRouteScope'
import { useProjectScopedLoader } from '@/composables/useProjectScopedLoader'
import { formatAbsoluteDateTime, formatRelativeDateTime } from '@/lib/stackos/time'
import { formatApiError } from '@/lib/client'
import { subscribeUsageUpdates, type UsageFeedState } from '@/lib/usageUpdates'
import { usageFeedKey } from '@/composables/useUsageFeed'
import { useSchedulesStore, type ScheduledJob } from '@/stores/schedules'
import type { DataTableColumn } from '@/components/types'
import ScheduleEditor from './ScheduleEditor.vue'
import ScheduledRuns from './ScheduledRuns.vue'
import { readableSchedule } from './schedulePresentation'

const store = useSchedulesStore()
const { projectId } = useProjectRouteScope()
const { items, loading, error, saving } = storeToRefs(store)
const activeTab = ref('automations')
const tabs = [{ key: 'automations', label: 'Automations' }, { key: 'runs', label: 'Runs' }]
const editing = ref(false)
const selectedJob = ref<ScheduledJob | null>(null)
const removing = ref<ScheduledJob | null>(null)
const actionError = ref<string | null>(null)
const itemsByNextRun = computed(() => [...items.value].sort((a, b) =>
  (a.next_run_at ? Date.parse(a.next_run_at) : Infinity) - (b.next_run_at ? Date.parse(b.next_run_at) : Infinity),
))
const columns: DataTableColumn<ScheduledJob>[] = [
  { key: 'name', label: 'Automation', widthClass: 'w-[26%]' },
  { key: 'cron_expr', label: 'Schedule', widthClass: 'w-[25%]' },
  { key: 'next_run_at', label: 'Next run', widthClass: 'w-[20%]' },
  { key: 'last_run_status', label: 'Last result', widthClass: 'w-[18%]' },
  { key: 'enabled', label: 'Enabled', widthClass: 'w-[11%]' },
]
let refreshing: Promise<unknown> | null = null
function load(): Promise<unknown> {
  if (!refreshing) refreshing = Promise.all([store.refresh(projectId.value), store.refreshRuns(projectId.value)]).finally(() => { refreshing = null })
  return refreshing
}
const { refresh } = useProjectScopedLoader({ projectId, load })
const feed = ref<UsageFeedState>({ status: 'connecting', receivedAt: null })
provide(usageFeedKey, feed)
const unsubscribe = subscribeUsageUpdates(projectId.value, {
  connection: state => { feed.value = state },
  usage: updates => { if (updates) store.applyUsage(updates); else void refresh() },
  status: () => { void refresh() }, resume: () => { void refresh() },
})
onBeforeUnmount(unsubscribe)

function edit(job: ScheduledJob | null): void { selectedJob.value = job; editing.value = true; actionError.value = null }
async function toggle(job: ScheduledJob, enabled: boolean): Promise<void> {
  actionError.value = null
  try { await store.toggle(projectId.value, job.id, enabled) }
  catch (cause) { actionError.value = formatApiError(cause) }
}
async function remove(): Promise<void> {
  if (!removing.value || saving.value) return
  actionError.value = null
  try { await store.remove(projectId.value, removing.value.id); removing.value = null }
  catch (cause) { actionError.value = formatApiError(cause) }
}
</script>

<template>
  <section class="automation-page min-w-0 space-y-5">
    <TabBar
      :tabs="tabs"
      :active-key="activeTab"
      aria-label="Automations"
      @change="activeTab = $event"
    />
    <UiCallout
      v-if="actionError"
      tone="danger"
      title="Automation change failed"
    >
      {{ actionError }}
    </UiCallout>
    <div
      v-if="activeTab === 'automations'"
      role="tabpanel"
      aria-labelledby="cs-tab-automations"
      class="space-y-4"
    >
      <div class="flex min-h-8 items-center justify-between gap-4">
        <p class="text-sm text-fg-muted">
          Runs only while StackOS is open. Missed runs are skipped.
        </p>
        <div class="flex shrink-0 items-center gap-2">
          <UiIconButton
            aria-label="Refresh automations"
            :disabled="loading"
            @click="refresh"
          >
            <UiIcon
              name="refresh"
              class="h-4 w-4"
            />
          </UiIconButton>
          <UiButton
            variant="primary"
            icon-left="plus"
            :disabled="saving"
            @click="edit(null)"
          >
            New automation
          </UiButton>
        </div>
      </div>
      <UiCallout
        v-if="error"
        tone="danger"
        title="Failed to load automations"
      >
        {{ error }}
      </UiCallout>
      <DataTable
        class="automations-table"
        :items="itemsByNextRun"
        :columns="columns"
        :loading="loading"
        aria-label="Automations"
        :empty-message="error ? 'Automation list unavailable. Refresh to retry.' : 'No automations yet. Create an automation to schedule a workflow or prompt.'"
      >
        <template #cell:name="{ row }">
          <div
            class="truncate font-medium text-fg-strong"
            :title="row.name ?? row.kind"
          >
            {{ row.name ?? row.kind }}
          </div>
          <div
            class="truncate text-xs text-fg-muted"
            :title="row.template_key ?? 'Prompt'"
          >
            {{ row.template_key ?? 'Prompt' }}
          </div>
        </template>
        <template #cell:cron_expr="{ row }">
          <div
            class="truncate"
            :title="row.cron_expr"
          >
            {{ readableSchedule(row.cron_expr) }}
          </div>
          <div
            class="truncate text-xs text-fg-muted"
            :title="row.timezone ?? 'Timezone not set'"
          >
            {{ row.timezone ?? 'Timezone not set' }}
          </div>
        </template>
        <template #cell:next_run_at="{ row }">
          <span
            class="block truncate"
            :title="formatAbsoluteDateTime(row.next_run_at)"
          >{{ row.next_run_at ? formatAbsoluteDateTime(row.next_run_at) : '—' }}</span>
        </template>
        <template #cell:last_run_status="{ row }">
          <div
            class="truncate"
            :title="row.setup_issue ?? undefined"
          >
            <StatusBadge
              v-if="row.setup_issue || row.last_run_status"
              kind="job"
              :status="row.setup_issue ? 'needs_setup' : row.last_run_status!"
              :label="row.setup_issue ? 'Needs setup' : undefined"
            />
            <span
              v-else
              class="text-fg-muted"
            >—</span>
          </div>
          <span
            v-if="row.last_run_at"
            class="block truncate text-xs text-fg-muted"
            :title="formatAbsoluteDateTime(row.last_run_at)"
          >{{ formatRelativeDateTime(row.last_run_at) }}</span>
        </template>
        <template #cell:enabled="{ row }">
          <div class="flex items-center justify-between gap-2">
            <UiSwitch
              :model-value="row.enabled"
              :disabled="saving"
              :aria-label="`Enable ${row.name ?? row.kind}`"
              @update:model-value="toggle(row, $event)"
            />
            <UiDropdownMenu
              placement="bottom-end"
              :aria-label="`Actions for ${row.name ?? row.kind}`"
              :items="[{ key: 'edit', label: 'Edit automation', disabled: saving, onSelect: () => edit(row) }, { key: 'remove', label: 'Remove automation', tone: 'danger', disabled: saving, onSelect: () => { removing = row; actionError = null } }]"
            >
              <template #trigger="{ toggle: toggleMenu, open }">
                <UiIconButton
                  :aria-label="`Actions for ${row.name ?? row.kind}`"
                  data-dropdown-trigger
                  aria-haspopup="menu"
                  :aria-expanded="open"
                  @click="toggleMenu"
                >
                  <UiIcon
                    name="more"
                    class="h-4 w-4 rotate-90"
                  />
                </UiIconButton>
              </template>
            </UiDropdownMenu>
          </div>
        </template>
      </DataTable>
    </div>
    <ScheduledRuns
      v-else
      :project-id="projectId"
      @refresh="refresh"
    />
    <ScheduleEditor
      v-if="editing"
      :project-id="projectId"
      :job="selectedJob"
      @close="editing = false"
      @saved="editing = false"
    />
    <UiConfirmDialog
      :model-value="!!removing"
      title="Remove automation?"
      :description="`Remove ${removing?.name ?? removing?.kind ?? 'this automation'}? Future runs stop; existing runs and history are retained.`"
      confirm-label="Remove automation"
      tone="danger"
      :loading="saving"
      @update:model-value="!$event && !saving && (removing = null)"
      @confirm="remove"
    >
      <UiCallout
        v-if="actionError"
        tone="danger"
        title="Could not remove automation"
      >
        {{ actionError }}
      </UiCallout>
    </UiConfirmDialog>
  </section>
</template>

<style scoped>
.automation-page :deep(table) { width: 100%; table-layout: fixed; }
.automation-page :deep(td) { height: 72px; }
.automation-page :deep(.ui-badge) { max-width: 100%; overflow: hidden; text-overflow: ellipsis; }
.automations-table :deep(.overflow-x-auto) { overflow: visible; }
</style>
