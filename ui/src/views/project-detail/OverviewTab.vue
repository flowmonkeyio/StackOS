<script setup lang="ts">
// OverviewTab — the project's operational home.
//
// Hierarchy: attention first (what needs the operator now), then recent
// activity, then setup/inventory reference. Attention cards deep-link to
// pre-filtered views.

import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import DataTable from '@/components/DataTable.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { UiBadge, UiButton, UiCallout, UiCard, UiMetricCard, UiSectionHeader } from '@/components/ui'
import type { DataTableColumn } from '@/components/types'
import { apiFetch, formatApiError } from '@/lib/client'
import { callOperation } from '@/lib/operations'
import { formatAbsoluteDateTime, formatRelativeDateTime, newestFirst } from '@/lib/stackos/time'
import type {
  SchemaAuthStatusOut,
  SchemaIntegrationBudgetOut,
  SchemaPageResponseResourceRecordOut,
  SchemaPageResponseRunOut,
  SchemaPluginOut,
  SchemaResourceRecordOut,
  SchemaRunOut,
  SchemaScheduledJobOut,
  SchemaWorkflowTemplateListOut,
} from '@/api'

const route = useRoute()
const router = useRouter()

const projectId = computed(() => Number.parseInt(route.params.id as string, 10))
const loading = ref(false)
const error = ref<string | null>(null)
const plugins = ref<SchemaPluginOut[]>([])
const templates = ref(0)
const runPlans = ref(0)
const resourceRecords = ref<SchemaResourceRecordOut[]>([])
const resourceTotal = ref(0)
const runs = ref<SchemaRunOut[]>([])
const connections = ref(0)
const schedules = ref<SchemaScheduledJobOut[]>([])
const budgets = ref<SchemaIntegrationBudgetOut[]>([])
const runningTotal = ref(0)
const failedTotal = ref(0)
const unreadRequests = ref(0)

const enabledPlugins = computed(() =>
  plugins.value.filter((plugin) => plugin.enabled_for_project !== false),
)
const activeSchedules = computed(() => schedules.value.filter((schedule) => schedule.enabled))
const activeBudgets = computed(() => budgets.value.filter((budget) => budget.monthly_budget_usd > 0))

const runColumns: DataTableColumn<SchemaRunOut>[] = [
  { key: 'id', label: 'ID', widthClass: 'w-16', cellClass: 'font-mono text-xs', format: (value) => `#${value}` },
  { key: 'kind', label: 'Kind' },
  { key: 'status', label: 'Status' },
  { key: 'last_step', label: 'Last step', format: (value) => String(value ?? '—') },
  { key: 'started_at', label: 'Started' },
]

const resourceColumns: DataTableColumn<SchemaResourceRecordOut>[] = [
  { key: 'plugin_slug', label: 'Plugin' },
  { key: 'resource_key', label: 'Resource', cellClass: 'font-mono text-xs' },
  { key: 'title', label: 'Title', cellClass: 'font-medium text-fg-strong', format: (value) => String(value ?? '—') },
  { key: 'updated_at', label: 'Updated' },
]

async function fetchOr<T>(path: string, fallback: T): Promise<T> {
  try {
    return await apiFetch<T>(path)
  } catch {
    return fallback
  }
}

async function fetchRunTotal(id: number, status: string): Promise<number> {
  const page = await fetchOr<SchemaPageResponseRunOut>(
    `/api/v1/projects/${id}/runs?status=${status}&limit=1`,
    { items: [], next_cursor: null, total_estimate: 0 },
  )
  return page.total_estimate ?? page.items.length
}

async function fetchUnreadRequests(id: number): Promise<number> {
  try {
    const page = await callOperation<{ items: unknown[]; total_estimate?: number | null }>(
      'agentRequest.list',
      { project_id: id, limit: 1, attention_status: 'unread' },
    )
    return page.total_estimate ?? page.items.length
  } catch {
    return 0
  }
}

async function load(): Promise<void> {
  if (!projectId.value || Number.isNaN(projectId.value)) return
  loading.value = true
  error.value = null
  try {
    const id = projectId.value
    const [
      pluginRows,
      templateRows,
      runPage,
      resourcePage,
      authStatus,
      scheduleRows,
      budgetRows,
      runningCount,
      failedCount,
      unreadCount,
    ] = await Promise.all([
      apiFetch<SchemaPluginOut[]>(`/api/v1/plugins?project_id=${id}`),
      fetchOr<SchemaWorkflowTemplateListOut>(
        `/api/v1/projects/${id}/workflow-templates`,
        { templates: [], include_shadowed: false },
      ),
      // The API pages ascending; pull a wide window so "recent" is truthful,
      // then sort newest-first and trim for display.
      fetchOr<SchemaPageResponseRunOut>(
        `/api/v1/projects/${id}/runs?limit=100`,
        { items: [], next_cursor: null, total_estimate: 0 },
      ),
      fetchOr<SchemaPageResponseResourceRecordOut>(
        `/api/v1/projects/${id}/resource-records?limit=100`,
        { items: [], next_cursor: null, total_estimate: 0 },
      ),
      fetchOr<SchemaAuthStatusOut>(
        `/api/v1/projects/${id}/auth/status`,
        { project_id: id, provider_key: null, providers: [], connections: [] },
      ),
      fetchOr<SchemaScheduledJobOut[]>(`/api/v1/projects/${id}/schedules`, []),
      fetchOr<SchemaIntegrationBudgetOut[]>(`/api/v1/projects/${id}/budgets`, []),
      fetchRunTotal(id, 'running'),
      fetchRunTotal(id, 'failed'),
      fetchUnreadRequests(id),
    ])
    plugins.value = pluginRows
    templates.value = templateRows.templates.length
    runs.value = newestFirst(runPage.items, (run) => run.started_at).slice(0, 8)
    runPlans.value = runPage.items.filter((run) => run.kind === 'run-plan').length
    resourceRecords.value = newestFirst(
      resourcePage.items,
      (record) => record.updated_at ?? record.created_at,
    ).slice(0, 8)
    resourceTotal.value = resourcePage.total_estimate ?? resourcePage.items.length
    connections.value = authStatus.connections.filter(
      (connection) => connection.status !== 'revoked',
    ).length
    schedules.value = scheduleRows
    budgets.value = budgetRows
    runningTotal.value = runningCount
    failedTotal.value = failedCount
    unreadRequests.value = unreadCount
  } catch (err) {
    error.value = formatApiError(err, 'failed to load project overview')
  } finally {
    loading.value = false
  }
}

function go(path: string): void {
  void router.push(`/projects/${projectId.value}/${path}`)
}

const base = computed(() => `/projects/${projectId.value}`)

onMounted(load)
</script>

<template>
  <div class="space-y-5">
    <UiCallout
      v-if="error"
      tone="danger"
    >
      {{ error }}
    </UiCallout>

    <div class="grid gap-4 md:grid-cols-3">
      <UiMetricCard
        label="Running now"
        :value="runningTotal"
        :value-tone="runningTotal > 0 ? 'info' : 'default'"
        icon="runs"
        :to="`${base}/runs?status=running`"
        :loading="loading"
      />
      <UiMetricCard
        label="Failed runs"
        :value="failedTotal"
        :value-tone="failedTotal > 0 ? 'danger' : 'success'"
        icon="alert-triangle"
        :to="`${base}/runs?status=failed`"
        :loading="loading"
      />
      <UiMetricCard
        label="Unread agent requests"
        :value="unreadRequests"
        :value-tone="unreadRequests > 0 ? 'accent' : 'default'"
        icon="inbox"
        :to="`${base}/agent-requests?attention_status=unread`"
        :loading="loading"
      />
    </div>

    <div class="grid gap-5 xl:grid-cols-[minmax(0,1.2fr)_minmax(320px,0.8fr)]">
      <section aria-label="Recent runs">
        <UiSectionHeader
          title="Recent runs"
          as="h3"
        >
          <template #actions>
            <UiBadge
              v-if="runningTotal"
              tone="info"
              dot
              pulse
            >
              {{ runningTotal }} running
            </UiBadge>
            <UiButton
              size="sm"
              variant="secondary"
              @click="go('runs')"
            >
              View all
            </UiButton>
          </template>
        </UiSectionHeader>
        <DataTable
          :items="runs"
          :columns="runColumns"
          :loading="loading"
          aria-label="Recent runs"
          empty-message="No runs yet — agents create runs when they execute workflows."
        >
          <template #cell:status="{ value }">
            <StatusBadge
              :status="String(value)"
              kind="run"
            />
          </template>
          <template #cell:started_at="{ value }">
            <span :title="formatAbsoluteDateTime(String(value))">
              {{ formatRelativeDateTime(String(value)) }}
            </span>
          </template>
        </DataTable>
      </section>

      <UiCard
        section
        :padded="false"
      >
        <template #header>
          <h3 class="t-h3 text-fg-strong">
            Setup &amp; inventory
          </h3>
          <UiButton
            size="sm"
            variant="secondary"
            @click="go('setup')"
          >
            Setup status
          </UiButton>
        </template>
        <nav aria-label="Project inventory">
          <ul class="divide-y divide-border-subtle text-sm">
            <li>
              <RouterLink
                :to="`${base}/plugins`"
                class="focus-ring-inset flex items-center justify-between gap-3 px-4 py-2.5 transition-colors duration-fast hover:bg-bg-surface-alt"
              >
                <span class="text-fg-muted">Enabled plugins</span>
                <span class="font-medium tabular-nums text-fg-strong">{{ enabledPlugins.length }}</span>
              </RouterLink>
            </li>
            <li>
              <RouterLink
                :to="`${base}/workflow-templates`"
                class="focus-ring-inset flex items-center justify-between gap-3 px-4 py-2.5 transition-colors duration-fast hover:bg-bg-surface-alt"
              >
                <span class="text-fg-muted">Workflow templates</span>
                <span class="font-medium tabular-nums text-fg-strong">{{ templates }}</span>
              </RouterLink>
            </li>
            <li>
              <RouterLink
                :to="`${base}/resources`"
                class="focus-ring-inset flex items-center justify-between gap-3 px-4 py-2.5 transition-colors duration-fast hover:bg-bg-surface-alt"
              >
                <span class="text-fg-muted">Resource records</span>
                <span class="font-medium tabular-nums text-fg-strong">{{ resourceTotal }}</span>
              </RouterLink>
            </li>
            <li>
              <RouterLink
                :to="`${base}/connections`"
                class="focus-ring-inset flex items-center justify-between gap-3 px-4 py-2.5 transition-colors duration-fast hover:bg-bg-surface-alt"
              >
                <span class="text-fg-muted">Connections</span>
                <span class="font-medium tabular-nums text-fg-strong">{{ connections }}</span>
              </RouterLink>
            </li>
            <li>
              <RouterLink
                :to="`${base}/schedules`"
                class="focus-ring-inset flex items-center justify-between gap-3 px-4 py-2.5 transition-colors duration-fast hover:bg-bg-surface-alt"
              >
                <span class="text-fg-muted">Active schedules</span>
                <span class="font-medium tabular-nums text-fg-strong">{{ activeSchedules.length }}</span>
              </RouterLink>
            </li>
            <li>
              <RouterLink
                :to="`${base}/cost-budget`"
                class="focus-ring-inset flex items-center justify-between gap-3 px-4 py-2.5 transition-colors duration-fast hover:bg-bg-surface-alt"
              >
                <span class="text-fg-muted">Budgets</span>
                <span class="font-medium tabular-nums text-fg-strong">{{ activeBudgets.length }}</span>
              </RouterLink>
            </li>
            <li>
              <RouterLink
                :to="`${base}/action-calls`"
                class="focus-ring-inset flex items-center justify-between gap-3 px-4 py-2.5 transition-colors duration-fast hover:bg-bg-surface-alt"
              >
                <span class="text-fg-muted">Run-plan audit rows</span>
                <span class="font-medium tabular-nums text-fg-strong">{{ runPlans }}</span>
              </RouterLink>
            </li>
          </ul>
        </nav>
      </UiCard>
    </div>

    <section aria-label="Latest resource records">
      <UiSectionHeader
        title="Latest resource records"
        as="h3"
      >
        <template #actions>
          <UiButton
            size="sm"
            variant="secondary"
            @click="go('resources')"
          >
            View all
          </UiButton>
        </template>
      </UiSectionHeader>
      <DataTable
        :items="resourceRecords"
        :columns="resourceColumns"
        :loading="loading"
        aria-label="Latest resource records"
        empty-message="No resource records yet — records appear as plugins store data."
      >
        <template #cell:plugin_slug="{ value }">
          <UiBadge tone="accent">{{ value }}</UiBadge>
        </template>
        <template #cell:updated_at="{ value }">
          <span :title="formatAbsoluteDateTime(String(value))">
            {{ formatRelativeDateTime(String(value)) }}
          </span>
        </template>
      </DataTable>
    </section>
  </div>
</template>
