<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute } from 'vue-router'

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
import { apiFetch, formatApiError } from '@/lib/client'
import { formatDateTime } from '@/lib/stackos/json'
import { useStackOsCatalogStore } from '@/stores/plugins'

import ActionCallDetailDrawer from './action-calls/ActionCallDetailDrawer.vue'

type StatusFilter = 'all' | 'running' | `${ActionCallStatus}`

const route = useRoute()
const catalogStore = useStackOsCatalogStore()
const { actions, enabledPlugins } = storeToRefs(catalogStore)

const projectId = computed(() => Number.parseInt(route.params.id as string, 10))
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
  return actions.value.find(
    (action) => action.plugin_slug === pluginSlug && action.key === actionKey,
  ) ?? null
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

function newestFirst(
  items: SchemaActionCallAuditOut[],
): SchemaActionCallAuditOut[] {
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
  return params.toString()
}

async function fetchCalls({ append = false }: { append?: boolean } = {}): Promise<void> {
  if (!projectId.value || Number.isNaN(projectId.value)) return
  loading.value = true
  error.value = null
  try {
    const response = await apiFetch<SchemaPageResponseActionCallAuditOut>(
      `/api/v1/projects/${projectId.value}/action-calls?${buildQuery(append ? nextCursor.value : null)}`,
    )
    // Sort the combined window, not per page — appended cursor pages would
    // otherwise interleave older/newer blocks.
    const nextRows = newestFirst(
      append ? [...rows.value, ...response.items] : [...response.items],
    )
    rows.value = nextRows
    nextCursor.value = response.next_cursor ?? null
    if (!append && selectedCall.value && !nextRows.some((row) => row.id === selectedCall.value?.id)) {
      selectedCall.value = null
      detailPanelOpen.value = false
    }
  } catch (err) {
    error.value = formatApiError(err, 'failed to load action calls')
  } finally {
    loading.value = false
  }
}

async function load(): Promise<void> {
  if (!projectId.value || Number.isNaN(projectId.value)) return
  await catalogStore.refresh(projectId.value)
  await fetchCalls()
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

onMounted(load)
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
