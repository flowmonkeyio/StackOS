<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
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
  UiFormField,
  UiInput,
  UiJsonBlock,
  UiPageShell,
  UiPanel,
  UiSectionHeader,
  UiSegmentedControl,
  UiSelect,
} from '@/components/ui'
import type { DataTableColumn } from '@/components/types'
import { apiFetch, formatApiError } from '@/lib/client'
import { formatDateTime, sanitizeForDisplay } from '@/lib/stackos/json'
import { useStackOsCatalogStore } from '@/stores/plugins'

type StatusFilter = 'all' | `${ActionCallStatus}`

const route = useRoute()
const catalogStore = useStackOsCatalogStore()
const { actions, enabledPlugins } = storeToRefs(catalogStore)

const projectId = computed(() => Number.parseInt(route.params.id as string, 10))
const rows = ref<SchemaActionCallAuditOut[]>([])
const selectedCall = ref<SchemaActionCallAuditOut | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
const nextCursor = ref<number | null>(null)
const pluginFilter = ref(String(route.query.plugin_slug ?? ''))
const actionFilter = ref(String(route.query.action_ref ?? ''))
const runFilter = ref(String(route.query.run_id ?? ''))
const statusFilter = ref<StatusFilter>('all')

const statusOptions: Array<{ key: StatusFilter; label: string }> = [
  { key: 'all', label: 'All' },
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
const loadedFailed = computed(
  () => rows.value.filter((call) => call.status === ActionCallStatus.failed).length,
)
const loadedDryRun = computed(
  () => rows.value.filter((call) => call.status === ActionCallStatus.dry_run).length,
)

const columns: DataTableColumn<SchemaActionCallAuditOut>[] = [
  { key: 'id', label: 'ID', widthClass: 'w-20', format: (value) => `#${value}` },
  { key: 'plugin_slug', label: 'Plugin', widthClass: 'w-28' },
  { key: 'action_key', label: 'Action' },
  { key: 'status', label: 'Status', widthClass: 'w-24' },
  { key: 'provider_key', label: 'Provider', format: (value) => String(value ?? '-') },
  { key: 'credential_ref', label: 'Credential', format: (value) => String(value ?? '-') },
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
    rows.value = append ? [...rows.value, ...response.items] : response.items
    nextCursor.value = response.next_cursor ?? null
    if (!append) selectedCall.value = response.items[0] ?? null
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

function callTitle(call: SchemaActionCallAuditOut): string {
  return `${call.plugin_slug}:${call.action_key}`
}

onMounted(load)
watch(projectId, load)
</script>

<template>
  <UiPageShell>
    <ProjectPageHeader
      :project-id="projectId"
      title="Action Calls"
      description="Audited external tool calls with redacted inputs, outputs, credential refs, and execution metadata."
      :breadcrumbs="[{ label: 'Action Calls' }]"
    />

    <UiCallout
      v-if="error"
      tone="danger"
    >
      {{ error }}
    </UiCallout>

    <div class="grid gap-3 md:grid-cols-4">
      <UiPanel class="p-3">
        <p class="text-xs text-fg-muted">Loaded calls</p>
        <p class="mt-1 text-2xl font-semibold text-fg-strong">{{ rows.length }}</p>
      </UiPanel>
      <UiPanel class="p-3">
        <p class="text-xs text-fg-muted">Success</p>
        <p class="mt-1 text-2xl font-semibold text-fg-strong">{{ loadedSuccess }}</p>
      </UiPanel>
      <UiPanel class="p-3">
        <p class="text-xs text-fg-muted">Failed</p>
        <p class="mt-1 text-2xl font-semibold text-fg-strong">{{ loadedFailed }}</p>
      </UiPanel>
      <UiPanel class="p-3">
        <p class="text-xs text-fg-muted">Dry runs</p>
        <p class="mt-1 text-2xl font-semibold text-fg-strong">{{ loadedDryRun }}</p>
      </UiPanel>
    </div>

    <UiPanel
      aria-label="Action call filters"
      class="p-4"
    >
      <UiSegmentedControl
        :model-value="statusFilter"
        :options="statusOptions"
        label="Action call status filter"
        @select="setStatus"
      />
      <div class="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-[220px_320px_160px_auto]">
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
        <UiFormField label="Run id">
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
            icon-left="rotate-ccw"
            @click="resetFilters"
          >
            Reset
          </UiButton>
        </div>
      </div>
    </UiPanel>

    <UiPanel class="p-4">
      <UiSectionHeader
        title="Audit Ledger"
        as="h3"
      >
        <template #actions>
          <UiBadge>{{ rows.length }}</UiBadge>
        </template>
      </UiSectionHeader>
      <DataTable
        :items="rows"
        :columns="columns"
        :loading="loading"
        :next-cursor="nextCursor"
        aria-label="Action call audit rows"
        empty-message="No action calls match these filters."
        interactive
        @row-click="(row) => (selectedCall = row)"
        @load-more="fetchCalls({ append: true })"
      >
        <template #cell:plugin_slug="{ value }">
          <UiBadge tone="accent">{{ value }}</UiBadge>
        </template>
        <template #cell:action_key="{ row }">
          <span class="font-mono text-xs">{{ callTitle(row) }}</span>
        </template>
        <template #cell:status="{ value }">
          <StatusBadge
            :status="String(value)"
            kind="job"
            :small="true"
          />
        </template>
        <template #cell:credential_ref="{ value }">
          <span class="font-mono text-xs">{{ value ?? '-' }}</span>
        </template>
      </DataTable>
    </UiPanel>

    <UiPanel
      v-if="selectedCall"
      class="p-4"
    >
      <UiSectionHeader
        :title="`Action Call #${selectedCall.id}`"
        :description="callTitle(selectedCall)"
        as="h3"
      >
        <template #actions>
          <StatusBadge
            :status="selectedCall.status"
            kind="job"
            :small="true"
          />
          <UiBadge v-if="selectedCall.credential_ref">
            {{ selectedCall.credential_ref }}
          </UiBadge>
        </template>
      </UiSectionHeader>
      <dl class="mb-3 grid gap-3 text-sm md:grid-cols-3 xl:grid-cols-6">
        <div>
          <dt class="text-xs text-fg-muted">Provider</dt>
          <dd>{{ selectedCall.provider_key ?? '-' }}</dd>
        </div>
        <div>
          <dt class="text-xs text-fg-muted">Connector</dt>
          <dd>{{ selectedCall.connector_key ?? '-' }}</dd>
        </div>
        <div>
          <dt class="text-xs text-fg-muted">Operation</dt>
          <dd class="truncate">{{ selectedCall.operation }}</dd>
        </div>
        <div>
          <dt class="text-xs text-fg-muted">Run</dt>
          <dd>{{ selectedCall.run_id ? `#${selectedCall.run_id}` : '-' }}</dd>
        </div>
        <div>
          <dt class="text-xs text-fg-muted">Cost</dt>
          <dd>{{ selectedCall.cost_cents }} cents</dd>
        </div>
        <div>
          <dt class="text-xs text-fg-muted">Created</dt>
          <dd>{{ formatDateTime(selectedCall.created_at) }}</dd>
        </div>
      </dl>
      <UiCallout
        v-if="selectedCall.error"
        tone="danger"
        density="compact"
        class="mb-3"
      >
        {{ selectedCall.error }}
      </UiCallout>
      <div class="grid gap-3 lg:grid-cols-3">
        <UiJsonBlock
          :data="sanitizeForDisplay(selectedCall.request_json ?? {})"
          density="compact"
          max-height="18rem"
          wrap
          aria-label="Action call request"
        />
        <UiJsonBlock
          :data="sanitizeForDisplay(selectedCall.response_json ?? {})"
          density="compact"
          max-height="18rem"
          wrap
          aria-label="Action call response"
        />
        <UiJsonBlock
          :data="sanitizeForDisplay(selectedCall.metadata_json ?? {})"
          density="compact"
          max-height="18rem"
          wrap
          aria-label="Action call metadata"
        />
      </div>
    </UiPanel>
  </UiPageShell>
</template>
