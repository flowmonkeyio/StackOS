<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import DataTable from '@/components/DataTable.vue'
import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import {
  UiBadge,
  UiButton,
  UiCallout,
  UiCheckbox,
  UiDescriptionItem,
  UiDescriptionList,
  UiFormField,
  UiJsonBlock,
  UiMetricCard,
  UiPageShell,
  UiPanel,
  UiSectionHeader,
  UiSegmentedControl,
  UiSelect,
} from '@/components/ui'
import type { DataTableColumn } from '@/components/types'
import { formatApiError } from '@/lib/client'
import { callOperation } from '@/lib/operations'
import { formatDateTime, sanitizeForDisplay } from '@/lib/stackos/json'

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
const rows = ref<AgentRequestOut[]>([])
const selectedRequest = ref<AgentRequestOut | null>(null)
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

const columns: DataTableColumn<AgentRequestOut>[] = [
  { key: 'id', label: 'ID', widthClass: 'w-20', format: (value) => `#${value}` },
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
    label: 'Run Plan',
    widthClass: 'w-28',
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
    rows.value = append ? [...rows.value, ...page.items] : page.items
    nextCursor.value = page.next_cursor ?? null
    totalEstimate.value = page.total_estimate ?? page.items.length
    if (!append && autoSelectNewest.value) selectedRequest.value = page.items[0] ?? null
  } catch (err) {
    error.value = formatApiError(err, 'failed to load agent requests')
  } finally {
    loading.value = false
  }
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
}

function resetFilters(): void {
  mode.value = 'claimable'
  attentionFilter.value = 'all'
  autoSelectNewest.value = true
  void fetchRequests()
}

function attentionTone(status: AgentRequestAttentionStatus): 'neutral' | 'accent' | 'success' {
  if (status === 'unread') return 'accent'
  if (status === 'archived') return 'neutral'
  return 'success'
}

function requestStatusKind(_status: AgentRequestStatus): 'job' {
  return 'job'
}

onMounted(fetchRequests)
watch(projectId, () => {
  autoSelectNewest.value = true
  void fetchRequests()
})
</script>

<template>
  <UiPageShell>
    <ProjectPageHeader
      :project-id="projectId"
      title="Agent Requests"
      description="Generic claimable queue state for agents, scripts, triggers, and project-local tooling."
      :breadcrumbs="[{ label: 'Agent Requests' }]"
    />

    <UiCallout
      v-if="error"
      tone="danger"
    >
      {{ error }}
    </UiCallout>

    <div class="grid gap-3 md:grid-cols-4">
      <UiMetricCard label="Loaded" :value="rows.length" density="compact" />
      <UiMetricCard label="New" :value="loadedNew" density="compact" />
      <UiMetricCard label="Claimed" :value="loadedClaimed" density="compact" />
      <UiMetricCard label="Unread" :value="loadedUnread" density="compact" />
    </div>

    <UiPanel
      aria-label="Agent request filters"
      class="p-4"
    >
      <UiSegmentedControl
        :model-value="mode"
        :options="modeOptions"
        label="Agent request mode"
        @select="setMode"
      />
      <div class="mt-3 grid gap-3 md:grid-cols-[260px_1fr_auto]">
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
            description="Keeps the detail panel synced to the first row after filter changes."
          />
        </div>
        <div class="flex items-end justify-start md:justify-end">
          <UiButton
            icon-left="rotate-ccw"
            @click="resetFilters"
          >
            Reset
          </UiButton>
        </div>
      </div>
    </UiPanel>

    <div class="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(22rem,28rem)] xl:items-start">
      <UiPanel class="p-4">
        <UiSectionHeader
          title="Queue"
          as="h3"
        >
          <template #actions>
            <UiBadge>{{ totalEstimate }} total</UiBadge>
            <UiBadge>{{ loadedTerminal }} terminal loaded</UiBadge>
          </template>
        </UiSectionHeader>
        <DataTable
          :items="rows"
          :columns="columns"
          :loading="loading"
          :next-cursor="nextCursor"
          :selected-id="selectedRequest?.id"
          max-height="calc(100vh - 24rem)"
          aria-label="Agent request queue"
          empty-message="No agent requests match these filters."
          interactive
          @row-click="selectRequest"
          @load-more="fetchRequests({ append: true })"
        >
          <template #cell:title="{ row }">
            <div class="max-w-xl">
              <p class="truncate font-medium text-fg-strong">{{ row.title }}</p>
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
              :status="String(value)"
              :kind="requestStatusKind(String(value) as AgentRequestStatus)"
              :small="true"
            />
          </template>
          <template #cell:attention_status="{ value }">
            <UiBadge :tone="attentionTone(value as AgentRequestAttentionStatus)">
              {{ value }}
            </UiBadge>
          </template>
          <template #cell:source_provider="{ row }">
            <span class="flex flex-wrap gap-1">
              <UiBadge v-if="row.source_provider">{{ row.source_provider }}</UiBadge>
              <UiBadge
                v-if="row.source_kind"
                tone="accent"
              >
                {{ row.source_kind }}
              </UiBadge>
              <span
                v-if="!row.source_provider && !row.source_kind"
                class="text-fg-muted"
              >
                -
              </span>
            </span>
          </template>
        </DataTable>
      </UiPanel>

      <UiPanel
        v-if="selectedRequest"
        class="p-4 xl:sticky xl:top-4 xl:max-h-[calc(100vh-2rem)] xl:overflow-y-auto"
      >
        <UiSectionHeader
          :title="`Request #${selectedRequest.id}`"
          :description="selectedRequest.request_key"
          as="h3"
        >
          <template #actions>
            <StatusBadge
              :status="selectedRequest.status"
              :kind="requestStatusKind(selectedRequest.status)"
              :small="true"
            />
            <UiBadge :tone="attentionTone(selectedRequest.attention_status)">
              {{ selectedRequest.attention_status }}
            </UiBadge>
          </template>
        </UiSectionHeader>

        <div class="space-y-3">
          <div>
            <p class="text-sm font-semibold text-fg-strong">{{ selectedRequest.title }}</p>
            <p
              v-if="selectedRequest.body_preview"
              class="mt-1 whitespace-pre-wrap text-sm text-fg-muted"
            >
              {{ selectedRequest.body_preview }}
            </p>
          </div>
          <UiDescriptionList bordered>
            <UiDescriptionItem
              label="Source provider"
              :value="selectedRequest.source_provider ?? '-'"
            />
            <UiDescriptionItem
              label="Source kind"
              :value="selectedRequest.source_kind ?? '-'"
            />
            <UiDescriptionItem
              label="Source resource"
              :value="selectedRequest.source_resource_key ?? '-'"
            />
            <UiDescriptionItem
              label="Source record"
              :value="selectedRequest.source_resource_record_id ? `#${selectedRequest.source_resource_record_id}` : '-'"
            />
            <UiDescriptionItem
              label="Source message"
              :value="selectedRequest.source_message_ref ?? '-'"
            />
            <UiDescriptionItem
              label="Claimed by"
              :value="selectedRequest.claimed_by ?? '-'"
            />
            <UiDescriptionItem
              label="Run plan"
              :value="selectedRequest.run_plan_id ? `#${selectedRequest.run_plan_id}` : '-'"
            />
            <UiDescriptionItem
              label="Updated"
              :value="formatDateTime(selectedRequest.updated_at)"
            />
          </UiDescriptionList>
          <UiJsonBlock
            :data="sanitizeForDisplay(selectedRequest.metadata_json ?? {})"
            density="compact"
            max-height="22rem"
            wrap
            aria-label="Agent request metadata"
          />
        </div>
      </UiPanel>
    </div>
  </UiPageShell>
</template>
