<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { onBeforeRouteUpdate, useRoute, type LocationQuery } from 'vue-router'

import { ActionCallStatus } from '@/api'
import type {
  SchemaActionCallAuditOut,
  SchemaActionOut,
  SchemaPageResponseActionCallAuditOut,
} from '@/api'
import DataTable from '@/components/DataTable.vue'
import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import {
  UiBadge,
  UiButton,
  UiCallout,
  UiCountBadge,
  UiFormField,
  UiInput,
  UiMetricCard,
  UiPageShell,
  UiSectionHeader,
  UiSegmentedControl,
  UiSelect,
  UiToolbar,
} from '@/components/ui'
import type { DataTableColumn } from '@/components/types'
import { useProjectRouteScope } from '@/composables/useProjectRouteScope'
import { useProjectScopedLoader } from '@/composables/useProjectScopedLoader'
import { apiFetch, formatApiError } from '@/lib/client'
import { formatDateTime } from '@/lib/stackos/json'
import { useStackOsCatalogStore } from '@/stores/plugins'

import ActionCallDetailDrawer from './action-calls/ActionCallDetailDrawer.vue'

type StatusFilter = 'all' | 'running' | `${ActionCallStatus}`

const route = useRoute()
const catalogStore = useStackOsCatalogStore()
const { actions, enabledPlugins } = storeToRefs(catalogStore)

const { projectId, changesProjectScope } = useProjectRouteScope(route)
const rows = ref<SchemaActionCallAuditOut[]>([])
const selectedCall = ref<SchemaActionCallAuditOut | null>(null)
const detailPanelOpen = ref(false)
const loading = ref(false)
const error = ref<string | null>(null)
const nextCursor = ref<number | null>(null)
const pluginFilter = ref(String(route.query.plugin_slug ?? ''))
const actionFilter = ref(String(route.query.action_ref ?? ''))
const runFilter = ref(String(route.query.run_id ?? ''))
const statusFilter = ref<StatusFilter>('all')
const providerFilter = ref('')
const createdFrom = ref('')
const createdBefore = ref('')
const dryRunFilter = ref('')
const exactCallId = ref('')
let requestGeneration = 0

function applyQuery(query: LocationQuery): void {
  const scalar = (key: string) => (typeof query[key] === 'string' ? query[key] : '')
  providerFilter.value = scalar('provider_key')
  createdFrom.value = scalar('created_from')
  createdBefore.value = scalar('created_before')
  dryRunFilter.value = ['true', 'false'].includes(scalar('dry_run')) ? scalar('dry_run') : ''
  exactCallId.value = scalar('action_call_id')
  pluginFilter.value = scalar('plugin_slug')
  actionFilter.value = scalar('action_ref')
  runFilter.value = scalar('run_id')
  statusFilter.value = Object.values(ActionCallStatus).includes(
    scalar('status') as ActionCallStatus,
  )
    ? (scalar('status') as StatusFilter)
    : 'all'
}
applyQuery(route.query)
onBeforeUnmount(() => {
  requestGeneration += 1
})

const statusOptions: Array<{ key: StatusFilter; label: string }> = [
  { key: 'all', label: 'All' },
  { key: ActionCallStatus.running, label: 'Running' },
  { key: ActionCallStatus.success, label: 'Success' },
  { key: ActionCallStatus.failed, label: 'Failed' },
  { key: ActionCallStatus.dry_run, label: 'Dry run' },
]

const pluginOptions = computed(() => [
  { value: '', label: 'All plugins' },
  ...enabledPlugins.value.map((plugin) => ({ value: plugin.slug, label: plugin.name })),
])

const visibleActions = computed(() =>
  actions.value.filter(
    (action) => !pluginFilter.value || action.plugin_slug === pluginFilter.value,
  ),
)

const actionOptions = computed(() => [
  { value: '', label: 'All actions' },
  ...visibleActions.value.map((action) => ({
    value: actionRef(action),
    label: actionRef(action),
    group: action.plugin_slug,
  })),
])

const selectedAction = computed(() => {
  if (!actionFilter.value) return null
  const [pluginSlug, actionKey] = actionFilter.value.split(':')
  return (
    actions.value.find((action) => action.plugin_slug === pluginSlug && action.key === actionKey) ??
    null
  )
})

const loadedSuccess = computed(
  () => rows.value.filter((call) => call.status === ActionCallStatus.success).length,
)
const loadedRunning = computed(
  () => rows.value.filter((call) => call.status === ActionCallStatus.running).length,
)
const loadedFailed = computed(
  () => rows.value.filter((call) => call.status === ActionCallStatus.failed).length,
)
const loadedDryRun = computed(
  () => rows.value.filter((call) => call.status === ActionCallStatus.dry_run).length,
)

const columns: DataTableColumn<SchemaActionCallAuditOut>[] = [
  { key: 'id', label: 'Call', widthClass: 'w-80' },
  { key: 'status', label: 'Status', widthClass: 'w-24' },
  { key: 'provider_key', label: 'Provider', widthClass: 'w-40' },
  { key: 'run_id', label: 'Run', widthClass: 'w-32' },
  {
    key: 'created_at',
    label: 'Created',
    widthClass: 'w-40',
    format: (value) => formatDateTime(String(value)),
  },
  {
    key: 'duration_ms',
    label: 'Duration',
    widthClass: 'w-24',
    format: (value) => (value === null || value === undefined ? '-' : `${value}ms`),
  },
]

function newestFirst(items: SchemaActionCallAuditOut[]): SchemaActionCallAuditOut[] {
  return [...items].sort((left, right) => {
    const createdDiff = Date.parse(right.created_at) - Date.parse(left.created_at)
    return createdDiff || right.id - left.id
  })
}

function actionRef(action: SchemaActionOut): string {
  return `${action.plugin_slug}:${action.key}`
}

function selectedRunId(): number | null {
  if (!runFilter.value.trim()) return null
  const parsed = Number.parseInt(runFilter.value, 10)
  return Number.isNaN(parsed) || parsed < 1 ? null : parsed
}

function actionQueryParts(): { pluginSlug: string; actionKey: string } {
  if (selectedAction.value) {
    return { pluginSlug: selectedAction.value.plugin_slug, actionKey: selectedAction.value.key }
  }
  return { pluginSlug: pluginFilter.value, actionKey: '' }
}

function buildQuery(after?: number | null): string {
  const params = new URLSearchParams()
  params.set('limit', '50')
  if (after) params.set('after', String(after))
  const { pluginSlug, actionKey } = actionQueryParts()
  if (pluginSlug) params.set('plugin_slug', pluginSlug)
  if (actionKey) params.set('action_key', actionKey)
  const runId = selectedRunId()
  if (runId) params.set('run_id', String(runId))
  if (statusFilter.value !== 'all') params.set('status', statusFilter.value)
  if (providerFilter.value) params.set('provider_key', providerFilter.value)
  if (createdFrom.value) params.set('created_from', createdFrom.value)
  if (createdBefore.value) params.set('created_before', createdBefore.value)
  if (dryRunFilter.value) params.set('dry_run', dryRunFilter.value)
  if (exactCallId.value) params.set('action_call_id', exactCallId.value)
  return params.toString()
}

async function fetchCalls({
  append = false,
  scopedProjectId = projectId.value,
}: { append?: boolean; scopedProjectId?: number } = {}): Promise<void> {
  const request = ++requestGeneration
  loading.value = true
  error.value = null
  try {
    const response = await apiFetch<SchemaPageResponseActionCallAuditOut>(
      `/api/v1/projects/${scopedProjectId}/action-calls?${buildQuery(append ? nextCursor.value : null)}`,
    )
    if (request !== requestGeneration) return
    // Sort the combined window, not per page — appended cursor pages would
    // otherwise interleave older/newer blocks.
    const nextRows = newestFirst(append ? [...rows.value, ...response.items] : [...response.items])
    rows.value = nextRows
    nextCursor.value = response.next_cursor ?? null
    if (!append && exactCallId.value) {
      selectedCall.value = nextRows.find((row) => String(row.id) === exactCallId.value) ?? null
      detailPanelOpen.value = Boolean(selectedCall.value)
      if (!selectedCall.value)
        error.value = 'That action record is not available within these project filters.'
    }
    if (
      !append &&
      selectedCall.value &&
      !nextRows.some((row) => row.id === selectedCall.value?.id)
    ) {
      selectedCall.value = null
      detailPanelOpen.value = false
    }
  } catch (err) {
    if (request === requestGeneration)
      error.value = formatApiError(err, 'failed to load action calls')
  } finally {
    if (request === requestGeneration) loading.value = false
  }
}

async function load(scopedProjectId: number): Promise<void> {
  await catalogStore.refresh(scopedProjectId)
  await fetchCalls({ scopedProjectId })
}

function setStatus(value: string | number): void {
  statusFilter.value = String(value) as StatusFilter
  void fetchCalls()
}

function setPlugin(value: string | number | null): void {
  pluginFilter.value = String(value ?? '')
  if (selectedAction.value && selectedAction.value.plugin_slug !== pluginFilter.value) {
    actionFilter.value = ''
  }
  void fetchCalls()
}

function setAction(value: string | number | null): void {
  actionFilter.value = String(value ?? '')
  if (selectedAction.value) pluginFilter.value = selectedAction.value.plugin_slug
  void fetchCalls()
}

function setRun(value: string | number | null): void {
  runFilter.value = String(value ?? '')
}

function applyRunFilter(): void {
  void fetchCalls()
}

function resetFilters(): void {
  pluginFilter.value = ''
  actionFilter.value = ''
  runFilter.value = ''
  statusFilter.value = 'all'
  providerFilter.value = ''
  createdFrom.value = ''
  createdBefore.value = ''
  dryRunFilter.value = ''
  exactCallId.value = ''
  selectedCall.value = null
  detailPanelOpen.value = false
  void fetchCalls()
}

function metricFilterClass(status: StatusFilter): string[] {
  return [
    'focus-ring rounded-lg text-left transition-shadow duration-fast',
    statusFilter.value === status
      ? 'outline outline-2 outline-focus outline-offset-2 shadow-sm'
      : 'hover:shadow-sm',
  ]
}

function callTitle(call: SchemaActionCallAuditOut): string {
  return `${call.plugin_slug}:${call.action_key}`
}

function runLabel(call: SchemaActionCallAuditOut): string {
  if (call.run_plan_step_id) return `step #${call.run_plan_step_id}`
  if (call.run_plan_id) return `plan #${call.run_plan_id}`
  if (call.run_id) return `run #${call.run_id}`
  return '-'
}

function openCall(call: SchemaActionCallAuditOut): void {
  selectedCall.value = call
  detailPanelOpen.value = true
}

useProjectScopedLoader({
  projectId,
  load: ({ projectId }) => load(projectId),
})
onBeforeRouteUpdate((to) => {
  if (changesProjectScope(to)) return
  applyQuery(to.query)
  rows.value = []
  selectedCall.value = null
  detailPanelOpen.value = false
  nextCursor.value = null
  void fetchCalls()
})
</script>

<template>
  <UiPageShell>
    <ProjectPageHeader
      :project-id="projectId"
      title="Action calls"
      description="Audited external tool calls with redacted inputs, outputs, credential refs, and execution metadata."
      :breadcrumbs="[{ label: 'Action calls' }]"
    >
      <template #actions>
        <UiButton
          variant="secondary"
          size="sm"
          icon-left="refresh"
          :loading="loading"
          @click="fetchCalls()"
        >
          Refresh
        </UiButton>
      </template>
    </ProjectPageHeader>

    <UiCallout
      v-if="error"
      tone="danger"
    >
      {{ error }}
    </UiCallout>

    <div
      v-if="createdFrom || createdBefore || dryRunFilter || providerFilter || exactCallId"
      class="flex flex-wrap items-center gap-3 rounded-lg border border-subtle bg-bg-surface-alt px-4 py-3 text-xs text-fg-muted"
      aria-label="Action drilldown filters"
    >
      <span v-if="createdFrom || createdBefore">UTC window: {{ createdFrom || 'any start' }} → {{ createdBefore || 'any end' }} (end
        excluded)</span>
      <span v-if="dryRunFilter">{{
        dryRunFilter === 'false' ? 'Dry runs excluded' : 'Dry runs only'
      }}</span>
      <span v-if="providerFilter">Provider: {{ providerFilter }}</span>
      <span v-if="exactCallId">Call #{{ exactCallId }}</span>
      <UiButton
        variant="ghost"
        size="sm"
        @click="resetFilters"
      >
        Clear drilldown
      </UiButton>
    </div>

    <div class="grid gap-3 md:grid-cols-5">
      <button
        type="button"
        :class="metricFilterClass('all')"
        :aria-pressed="statusFilter === 'all'"
        aria-label="Show all action calls"
        @click="setStatus('all')"
      >
        <UiMetricCard
          label="Loaded calls"
          :value="rows.length"
          density="compact"
        />
      </button>
      <button
        type="button"
        :class="metricFilterClass('running')"
        :aria-pressed="statusFilter === 'running'"
        aria-label="Filter to running calls"
        @click="setStatus('running')"
      >
        <UiMetricCard
          label="Running"
          :value="loadedRunning"
          density="compact"
        />
      </button>
      <button
        type="button"
        :class="metricFilterClass('success')"
        :aria-pressed="statusFilter === 'success'"
        aria-label="Filter to successful calls"
        @click="setStatus('success')"
      >
        <UiMetricCard
          label="Success"
          :value="loadedSuccess"
          density="compact"
        />
      </button>
      <button
        type="button"
        :class="metricFilterClass('failed')"
        :aria-pressed="statusFilter === 'failed'"
        aria-label="Filter to failed calls"
        @click="setStatus('failed')"
      >
        <UiMetricCard
          label="Failed"
          :value="loadedFailed"
          :value-tone="loadedFailed > 0 ? 'danger' : 'default'"
          density="compact"
        />
      </button>
      <button
        type="button"
        :class="metricFilterClass('dry-run')"
        :aria-pressed="statusFilter === 'dry-run'"
        aria-label="Filter to dry runs"
        @click="setStatus('dry-run')"
      >
        <UiMetricCard
          label="Dry runs"
          :value="loadedDryRun"
          density="compact"
        />
      </button>
    </div>

    <UiToolbar
      variant="sunken"
      aria-label="Action call filters"
      density="comfortable"
    >
      <div class="flex w-full flex-col gap-3">
        <UiSegmentedControl
          :model-value="statusFilter"
          :options="statusOptions"
          label="Action call status filter"
          @select="setStatus"
        />
        <div class="grid gap-3 md:grid-cols-2 xl:grid-cols-[220px_320px_160px_auto]">
          <UiFormField label="Plugin">
            <UiSelect
              :model-value="pluginFilter"
              :options="pluginOptions"
              @update:model-value="setPlugin"
            />
          </UiFormField>
          <UiFormField label="Action">
            <UiSelect
              :model-value="actionFilter"
              :options="actionOptions"
              @update:model-value="setAction"
            />
          </UiFormField>
          <UiFormField label="Run">
            <UiInput
              type="number"
              min="1"
              :model-value="runFilter"
              @update:model-value="setRun"
              @change="applyRunFilter"
            />
          </UiFormField>
          <div class="flex items-end">
            <UiButton
              variant="secondary"
              size="sm"
              icon-left="refresh"
              @click="resetFilters"
            >
              Reset
            </UiButton>
          </div>
        </div>
      </div>
    </UiToolbar>

    <section aria-label="Action call audit ledger">
      <UiSectionHeader
        title="Audit ledger"
        description="Newest calls are listed first. Select a row to inspect redacted details."
        as="h3"
      >
        <template #actions>
          <UiCountBadge :value="rows.length" />
        </template>
      </UiSectionHeader>
      <DataTable
        :items="rows"
        :columns="columns"
        :loading="loading"
        :next-cursor="nextCursor"
        :selected-id="detailPanelOpen ? selectedCall?.id : null"
        aria-label="Action call audit rows"
        empty-message="No action calls match these filters — calls are recorded when agents execute actions."
        interactive
        @row-click="openCall"
        @load-more="fetchCalls({ append: true })"
      >
        <template #cell:id="{ row }">
          <div class="min-w-0">
            <div class="flex min-w-0 items-center gap-2">
              <span class="font-mono text-xs text-fg-muted">#{{ row.id }}</span>
              <UiBadge tone="accent">
                {{ row.plugin_slug }}
              </UiBadge>
            </div>
            <div class="mt-1 truncate font-mono text-xs text-fg-default">
              {{ callTitle(row) }}
            </div>
          </div>
        </template>
        <template #cell:status="{ value }">
          <StatusBadge
            :status="String(value)"
            kind="job"
            :small="true"
          />
        </template>
        <template #cell:provider_key="{ row }">
          <div class="min-w-0 text-sm">
            <div class="truncate">
              {{ row.provider_key ?? '-' }}
            </div>
            <div class="truncate text-xs text-fg-muted">
              {{ row.connector_key ?? '-' }}
            </div>
          </div>
        </template>
        <template #cell:run_id="{ row }">
          <span class="text-xs text-fg-muted">{{ runLabel(row) }}</span>
        </template>
      </DataTable>
    </section>

    <ActionCallDetailDrawer
      v-model="detailPanelOpen"
      :call="selectedCall"
    />
  </UiPageShell>
</template>
