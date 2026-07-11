<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import DataTable from '@/components/DataTable.vue'
import InspectableDetailDrawer from '@/components/InspectableDetailDrawer.vue'
import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import {
  UiAdvancedJsonPanel,
  UiBadge,
  UiButton,
  UiCallout,
  UiCheckbox,
  UiCountBadge,
  UiFormField,
  UiIcon,
  UiMetadataStrip,
  UiMetricCard,
  UiPageShell,
  UiSectionHeader,
  UiSegmentedControl,
  UiSelect,
  UiToolbar,
} from '@/components/ui'
import type { DataTableColumn } from '@/components/types'
import { formatApiError } from '@/lib/client'
import { callOperation } from '@/lib/operations'
import { formatDateTime, sanitizeForDisplay } from '@/lib/stackos/json'
import { newestFirst } from '@/lib/stackos/time'

type AgentRequestStatus =
  | 'new'
  | 'claimed'
  | 'run-created'
  | 'run-started'
  | 'responded'
  | 'resolved'
  | 'ignored'
  | 'failed'
type AgentRequestAttentionStatus = 'unread' | 'read' | 'archived'
type RequestMode = 'claimable' | 'all' | 'active' | 'terminal'
type AttentionFilter = 'all' | AgentRequestAttentionStatus

interface AgentRequestOut {
  id: number
  project_id: number
  request_key: string
  title: string
  body_preview: string
  source_provider: string | null
  source_kind: string | null
  source_resource_key: string | null
  source_resource_record_id: number | null
  source_message_ref: string | null
  priority: number
  status: AgentRequestStatus
  attention_status: AgentRequestAttentionStatus
  claimed_by: string | null
  claimed_at: string | null
  claim_expires_at: string | null
  run_plan_id: number | null
  completed_at: string | null
  ignored_at: string | null
  metadata_json: Record<string, unknown> | null
  created_at: string
  updated_at: string
}

interface AgentRequestPage {
  items: AgentRequestOut[]
  next_cursor: number | null
  total_estimate: number
}

const route = useRoute()

const projectId = computed(() => Number.parseInt(route.params.id as string, 10))
const requestedRequestId = computed(() => {
  const value = Number.parseInt(String(route.query.request ?? ''), 10)
  return Number.isNaN(value) ? null : value
})
const rows = ref<AgentRequestOut[]>([])
const selectedRequest = ref<AgentRequestOut | null>(null)
const detailOpen = ref(false)
const loading = ref(false)
const error = ref<string | null>(null)
const nextCursor = ref<number | null>(null)
const totalEstimate = ref(0)
const mode = ref<RequestMode>('claimable')
const attentionFilter = ref<AttentionFilter>('all')
const autoSelectNewest = ref(true)

const modeOptions: Array<{ key: RequestMode; label: string }> = [
  { key: 'claimable', label: 'Claimable' },
  { key: 'all', label: 'All' },
  { key: 'active', label: 'Active' },
  { key: 'terminal', label: 'Terminal' },
]

const attentionOptions = [
  { value: 'all', label: 'All attention states' },
  { value: 'unread', label: 'Unread' },
  { value: 'read', label: 'Read' },
  { value: 'archived', label: 'Archived' },
]

// Claimable mode is a ranked work queue — keep the daemon's order there.
// Every other mode is an audit list and leads with the latest request.
const displayRows = computed(() =>
  mode.value === 'claimable' ? rows.value : newestFirst(rows.value, (row) => row.created_at),
)

const columns: DataTableColumn<AgentRequestOut>[] = [
  { key: 'id', label: 'ID', widthClass: 'w-20', cellClass: 'font-mono text-xs', format: (value) => `#${value}` },
  { key: 'priority', label: 'Priority', widthClass: 'w-24' },
  { key: 'title', label: 'Title' },
  { key: 'status', label: 'Status', widthClass: 'w-32' },
  { key: 'attention_status', label: 'Attention', widthClass: 'w-28' },
  {
    key: 'source_provider',
    label: 'Source',
    widthClass: 'w-40',
    format: (value) => String(value ?? '-'),
  },
  {
    key: 'run_plan_id',
    label: 'Run plan',
    widthClass: 'w-28',
    cellClass: 'font-mono text-xs',
    format: (value) => (value === null || value === undefined ? '-' : `#${value}`),
  },
  {
    key: 'updated_at',
    label: 'Updated',
    widthClass: 'w-40',
    format: (value) => formatDateTime(String(value)),
  },
]

const loadedNew = computed(() => rows.value.filter((item) => item.status === 'new').length)
const loadedClaimed = computed(() => rows.value.filter((item) => item.status === 'claimed').length)
const loadedTerminal = computed(
  () => rows.value.filter((item) => ['resolved', 'ignored', 'failed'].includes(item.status)).length,
)
const loadedUnread = computed(
  () => rows.value.filter((item) => item.attention_status === 'unread').length,
)
const loadedActive = computed(
  () => rows.value.filter((item) => ['new', 'claimed', 'run-created', 'run-started', 'responded'].includes(item.status)).length,
)

const requestStages = [
  { key: 'arrived', label: 'Request arrives', detail: 'A plugin, trigger, or agent records an input.' },
  { key: 'claimed', label: 'Agent claims it', detail: 'A connected agent decides how to handle it.' },
  { key: 'work', label: 'Work becomes visible', detail: 'The task and run appear in Work for supervision.' },
  { key: 'outcome', label: 'Outcome is recorded', detail: 'Results and evidence appear in Activity.' },
] as const

function requestStageIndex(status: AgentRequestStatus): number {
  if (status === 'new') return 0
  if (status === 'claimed') return 1
  if (['run-created', 'run-started'].includes(status)) return 2
  return 3
}

function requestNextStep(request: AgentRequestOut): string {
  if (request.status === 'new') return 'Waiting for a connected agent to claim it.'
  if (request.status === 'claimed') return 'The claiming agent should create or link tracked work.'
  if (request.status === 'run-created') return 'The run is ready for the agent to start.'
  if (request.status === 'run-started') return 'Follow progress in Work; intervene only if Attention asks.'
  if (request.status === 'responded') return 'The agent responded; the request can now be resolved.'
  if (request.status === 'resolved') return 'Complete. Review the outcome and evidence in Activity.'
  if (request.status === 'failed') return 'Review the failure and any required human action.'
  return 'No action is expected for this request.'
}

function statusArguments(): AgentRequestStatus[] | undefined {
  if (mode.value === 'active') return ['new', 'claimed', 'run-created', 'run-started', 'responded']
  if (mode.value === 'terminal') return ['resolved', 'ignored', 'failed']
  return undefined
}

function buildArguments(after?: number | null): Record<string, unknown> {
  const args: Record<string, unknown> = {
    project_id: projectId.value,
    limit: 50,
  }
  if (after) args.after_id = after
  if (mode.value === 'claimable') {
    args.claimable = true
  } else {
    const statuses = statusArguments()
    if (statuses) args.statuses = statuses
  }
  if (attentionFilter.value !== 'all') args.attention_status = attentionFilter.value
  return args
}

async function fetchRequests({ append = false }: { append?: boolean } = {}): Promise<void> {
  if (!projectId.value || Number.isNaN(projectId.value)) return
  loading.value = true
  error.value = null
  try {
    const page = await callOperation<AgentRequestPage>(
      'agentRequest.list',
      buildArguments(append ? nextCursor.value : null),
    )
    const nextRows = append ? [...rows.value, ...page.items] : page.items
    rows.value = nextRows
    nextCursor.value = page.next_cursor ?? null
    totalEstimate.value = page.total_estimate ?? page.items.length
    reconcileSelectedRequest(nextRows, append)
  } catch (err) {
    error.value = formatApiError(err, 'failed to load agent requests')
  } finally {
    loading.value = false
  }
}

function reconcileSelectedRequest(nextRows: AgentRequestOut[], append: boolean): void {
  const requested = requestedRequestId.value
    ? nextRows.find((request) => request.id === requestedRequestId.value)
    : null
  if (!append && requested) {
    autoSelectNewest.value = false
    selectedRequest.value = requested
    detailOpen.value = true
  } else if (!append && autoSelectNewest.value) {
    selectedRequest.value = nextRows[0] ?? null
  } else if (selectedRequest.value) {
    selectedRequest.value =
      nextRows.find((request) => request.id === selectedRequest.value?.id) ?? null
  }
  if (!selectedRequest.value) detailOpen.value = false
}

function setMode(value: string | number): void {
  mode.value = String(value) as RequestMode
  void fetchRequests()
}

function setAttention(value: string | number | null): void {
  attentionFilter.value = String(value ?? 'all') as AttentionFilter
  void fetchRequests()
}

function selectRequest(row: AgentRequestOut): void {
  autoSelectNewest.value = false
  selectedRequest.value = row
  detailOpen.value = true
}

function resetFilters(): void {
  mode.value = 'claimable'
  attentionFilter.value = 'all'
  autoSelectNewest.value = true
  void fetchRequests()
}

function agentRequestCoreMetadata(request: AgentRequestOut) {
  const source = [request.source_provider, request.source_kind].filter(Boolean).join(' / ')
  return [
    { label: 'Priority', value: request.priority },
    { label: 'Source', value: source || '-', mono: Boolean(source) },
    { label: 'Run plan', value: request.run_plan_id ? `#${request.run_plan_id}` : '-' },
    { label: 'Updated', value: formatDateTime(request.updated_at) },
  ]
}

function agentRequestTraceMetadata(request: AgentRequestOut) {
  return [
    request.source_resource_key
      ? { label: 'Resource', value: request.source_resource_key, mono: true }
      : null,
    request.source_resource_record_id
      ? { label: 'Record', value: `#${request.source_resource_record_id}` }
      : null,
    request.source_message_ref
      ? { label: 'Message', value: request.source_message_ref, mono: true }
      : null,
    request.claimed_by ? { label: 'Claimed by', value: request.claimed_by } : null,
    request.claimed_at ? { label: 'Claimed', value: formatDateTime(request.claimed_at) } : null,
    request.claim_expires_at
      ? { label: 'Claim expires', value: formatDateTime(request.claim_expires_at) }
      : null,
    request.completed_at ? { label: 'Completed', value: formatDateTime(request.completed_at) } : null,
    request.ignored_at ? { label: 'Ignored', value: formatDateTime(request.ignored_at) } : null,
    { label: 'Created', value: formatDateTime(request.created_at) },
  ].filter((item): item is NonNullable<typeof item> => Boolean(item))
}

/** Deep-link support: `?attention_status=unread` arrives pre-filtered. */
function applyFiltersFromQuery(): void {
  const attention = String(route.query.attention_status ?? '')
  if (attention !== 'all' && attentionOptions.some((option) => option.value === attention)) {
    attentionFilter.value = attention as AttentionFilter
    // Attention filters should scan the whole queue, not just claimable rows.
    mode.value = 'all'
  }
}

onMounted(() => {
  applyFiltersFromQuery()
  void fetchRequests()
})
</script>

<template>
  <UiPageShell>
    <ProjectPageHeader
      :project-id="projectId"
      title="Agent requests"
      description="Inputs waiting for agents, and the handoff from request to visible work and recorded outcome."
      :breadcrumbs="[{ label: 'Agent requests' }]"
    >
      <template #actions>
        <UiButton
          variant="secondary"
          size="sm"
          icon-left="refresh"
          :loading="loading"
          @click="fetchRequests()"
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

    <section
      aria-labelledby="request-journey-title"
      class="rounded-xl border border-subtle bg-bg-surface p-4 shadow-xs"
    >
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2
            id="request-journey-title"
            class="text-sm font-semibold text-fg-strong"
          >
            From request to outcome
          </h2>
          <p class="mt-1 text-xs text-fg-muted">
            StackOS holds and exposes the request. A connected agent decides and acts through MCP; people supervise the resulting work here.
          </p>
        </div>
        <RouterLink
          :to="`/projects/${projectId}/tasks`"
          class="focus-ring inline-flex items-center gap-1 text-xs font-semibold text-fg-link"
        >
          Open Work
          <UiIcon
            name="arrow-right"
            class="h-3.5 w-3.5"
            aria-hidden="true"
          />
        </RouterLink>
      </div>
      <ol class="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <li
          v-for="(stage, index) in requestStages"
          :key="stage.key"
          class="rounded-lg border border-subtle bg-bg-surface-alt p-3"
        >
          <p class="text-2xs font-semibold uppercase tracking-wide text-accent-fg">
            {{ index + 1 }} · {{ stage.label }}
          </p>
          <p class="mt-1 text-xs leading-5 text-fg-muted">
            {{ stage.detail }}
          </p>
        </li>
      </ol>
    </section>

    <div class="grid gap-3 md:grid-cols-4">
      <UiMetricCard
        label="Waiting to be claimed"
        :value="loadedNew"
        density="compact"
        :value-tone="loadedNew > 0 ? 'info' : 'default'"
      />
      <UiMetricCard
        label="Active requests"
        :value="loadedActive"
        density="compact"
      />
      <UiMetricCard
        label="Claimed"
        :value="loadedClaimed"
        density="compact"
      />
      <UiMetricCard
        label="Needs review"
        :value="loadedUnread"
        density="compact"
        :value-tone="loadedUnread > 0 ? 'warning' : 'default'"
      />
    </div>

    <UiToolbar
      variant="sunken"
      aria-label="Agent request filters"
      density="comfortable"
    >
      <div class="flex w-full flex-col gap-3">
        <UiSegmentedControl
          :model-value="mode"
          :options="modeOptions"
          label="Agent request mode"
          @select="setMode"
        />
        <div class="grid gap-3 md:grid-cols-[260px_1fr_auto]">
          <UiFormField label="Attention">
            <UiSelect
              :model-value="attentionFilter"
              :options="attentionOptions"
              @update:model-value="setAttention"
            />
          </UiFormField>
          <div class="flex items-end">
            <UiCheckbox
              v-model="autoSelectNewest"
              label="Select newest on refresh"
              description="Keeps the selected row synced to the first row after filter changes."
            />
          </div>
          <div class="flex items-end justify-start md:justify-end">
            <UiButton
              variant="secondary"
              size="sm"
              icon-left="close"
              @click="resetFilters"
            >
              Reset filters
            </UiButton>
          </div>
        </div>
      </div>
    </UiToolbar>

    <section aria-label="Agent request queue">
      <UiSectionHeader
        title="Queue"
        as="h2"
      >
        <template #actions>
          <span class="inline-flex items-center gap-1.5 text-xs text-fg-muted">
            <UiCountBadge :value="totalEstimate" />
            total
          </span>
          <span class="inline-flex items-center gap-1.5 text-xs text-fg-muted">
            <UiCountBadge :value="loadedTerminal" />
            terminal loaded
          </span>
        </template>
      </UiSectionHeader>
      <DataTable
        :items="displayRows"
        :columns="columns"
        :loading="loading"
        :next-cursor="nextCursor"
        :selected-id="selectedRequest?.id"
        aria-label="Agent request queue"
        empty-message="No agent requests match these filters — requests are created by plugins, triggers, and agents for other agents to claim."
        interactive
        @row-click="selectRequest"
        @load-more="fetchRequests({ append: true })"
      >
        <template #cell:title="{ row }">
          <div class="max-w-xl">
            <p class="truncate font-medium text-fg-strong">
              {{ row.title }}
            </p>
            <p
              v-if="row.body_preview"
              class="mt-1 line-clamp-2 text-xs text-fg-muted"
            >
              {{ row.body_preview }}
            </p>
          </div>
        </template>
        <template #cell:status="{ value }">
          <StatusBadge
            domain="agentRequest"
            :status="String(value)"
            small
          />
        </template>
        <template #cell:attention_status="{ value }">
          <StatusBadge
            domain="attention"
            :status="String(value)"
            small
          />
        </template>
        <template #cell:source_provider="{ row }">
          <span class="flex flex-wrap gap-1">
            <UiBadge
              v-if="row.source_provider"
              variant="outline"
            >
              {{ row.source_provider }}
            </UiBadge>
            <UiBadge v-if="row.source_kind">{{ row.source_kind }}</UiBadge>
            <span
              v-if="!row.source_provider && !row.source_kind"
              class="text-fg-muted"
            >
              -
            </span>
          </span>
        </template>
      </DataTable>
    </section>

    <InspectableDetailDrawer
      v-model="detailOpen"
      :title="selectedRequest ? `Request #${selectedRequest.id}` : 'Agent request'"
      :description="selectedRequest?.request_key"
      size="lg"
      :has-detail="Boolean(selectedRequest)"
      empty-title="No request selected"
      empty-description="Select an agent request row to inspect its source, lifecycle, and metadata."
    >
      <template #header="{ titleId, descriptionId }">
        <div
          v-if="selectedRequest"
          class="min-w-0"
        >
          <div class="flex flex-wrap items-center gap-2">
            <h2
              :id="titleId"
              class="t-h2 text-fg-strong"
            >
              Request #{{ selectedRequest.id }}
            </h2>
            <StatusBadge
              domain="agentRequest"
              :status="selectedRequest.status"
              small
            />
            <StatusBadge
              domain="attention"
              :status="selectedRequest.attention_status"
              small
            />
          </div>
          <p
            :id="descriptionId"
            class="mt-1 truncate font-mono text-xs text-fg-muted"
          >
            {{ selectedRequest.request_key }}
          </p>
        </div>
      </template>

      <div
        v-if="selectedRequest"
        class="space-y-3"
      >
        <div>
          <p class="text-sm font-semibold text-fg-strong">
            {{ selectedRequest.title }}
          </p>
          <p
            v-if="selectedRequest.body_preview"
            class="mt-1 whitespace-pre-wrap text-sm text-fg-muted"
          >
            {{ selectedRequest.body_preview }}
          </p>
        </div>

        <UiMetadataStrip
          :items="agentRequestCoreMetadata(selectedRequest)"
          aria-label="Agent request metadata"
        />

        <section class="rounded-lg border border-subtle bg-bg-surface-alt p-4">
          <p class="t-overline text-fg-subtle">
            Where this request is now
          </p>
          <div class="mt-3 flex items-start gap-3">
            <span class="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent-subtle text-sm font-semibold text-accent-fg">
              {{ requestStageIndex(selectedRequest.status) + 1 }}
            </span>
            <div>
              <p class="text-sm font-semibold text-fg-strong">
                {{ requestStages[requestStageIndex(selectedRequest.status)].label }}
              </p>
              <p class="mt-1 text-xs leading-5 text-fg-muted">
                {{ requestNextStep(selectedRequest) }}
              </p>
            </div>
          </div>
          <div class="mt-4 flex flex-wrap gap-2">
            <RouterLink
              v-if="selectedRequest.run_plan_id"
              :to="`/projects/${projectId}/tasks`"
              class="focus-ring inline-flex h-8 items-center rounded-md bg-accent px-3 text-xs font-medium text-fg-on-accent"
            >
              Follow in Work
            </RouterLink>
            <RouterLink
              v-if="selectedRequest.run_plan_id"
              :to="`/projects/${projectId}/runs`"
              class="focus-ring inline-flex h-8 items-center rounded-md border border-default bg-bg-surface px-3 text-xs font-medium text-fg-default"
            >
              Inspect run
            </RouterLink>
            <RouterLink
              v-if="['responded', 'resolved', 'failed'].includes(selectedRequest.status)"
              :to="`/projects/${projectId}/activity`"
              class="focus-ring inline-flex h-8 items-center rounded-md border border-default bg-bg-surface px-3 text-xs font-medium text-fg-default"
            >
              Review Activity
            </RouterLink>
          </div>
        </section>

        <details class="rounded-md border border-subtle bg-bg-surface px-3 py-2">
          <summary class="cursor-pointer text-xs font-semibold uppercase text-fg-subtle">
            Trace
          </summary>
          <UiMetadataStrip
            class="mt-2"
            :items="agentRequestTraceMetadata(selectedRequest)"
            aria-label="Agent request trace metadata"
          />
        </details>

        <UiAdvancedJsonPanel
          title="Metadata"
          summary="Raw request metadata"
          :data="sanitizeForDisplay(selectedRequest.metadata_json ?? {})"
          max-height="22rem"
        />
      </div>
    </InspectableDetailDrawer>
  </UiPageShell>
</template>
