<script setup lang="ts">
// RunsView — audit trail with kind/status/parent filters.
//
// Per audit M-29: the per-step grain is shown in `RunDetailView` (sibling
// component) which is wired through the `:run_id?` sub-route. Selecting
// a row navigates to /projects/:id/runs/:run_id.
//
// Wires to read-only run listing/detail endpoints.

import { computed, onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute, useRouter } from 'vue-router'

import DataTable from '@/components/DataTable.vue'
import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import {
  UiButton,
  UiCallout,
  UiFormField,
  UiInput,
  UiPageShell,
  UiPanel,
  UiSegmentedControl,
  UiSelect,
} from '@/components/ui'
import RunDetail from './RunDetail.vue'
import { useRunsStore, type Run } from '@/stores/runs'
import { RunKind as RunKindEnum, RunStatus as RunStatusEnum } from '@/api'
import type { DataTableColumn } from '@/components/types'

const route = useRoute()
const router = useRouter()
const runsStore = useRunsStore()

const projectId = computed<number>(() => Number.parseInt(route.params.id as string, 10))
const runId = computed<number | null>(() => {
  const raw = route.params.run_id
  if (!raw || raw === '') return null
  const n = Number.parseInt(String(raw), 10)
  return Number.isNaN(n) ? null : n
})

const { filteredItems, loading, nextCursor, error, filters } = storeToRefs(runsStore)

const STATUS_OPTIONS: { key: 'all' | `${RunStatusEnum}`; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'running', label: 'Running' },
  { key: 'success', label: 'Success' },
  { key: 'failed', label: 'Failed' },
  { key: 'aborted', label: 'Aborted' },
]

const KIND_OPTIONS = Object.values(RunKindEnum)
const kindOptions = computed(() => [
  { value: '', label: 'All kinds' },
  ...KIND_OPTIONS.map((kind) => ({ value: kind, label: kind })),
])

const columns: DataTableColumn<Run>[] = [
  { key: 'id', label: 'ID', widthClass: 'w-20' },
  { key: 'kind', label: 'Kind' },
  { key: 'status', label: 'Status', widthClass: 'w-24' },
  { key: 'parent_run_id', label: 'Parent', widthClass: 'w-20' },
  {
    key: 'started_at',
    label: 'Started',
    sortable: true,
    format: (v) => (v ? new Date(String(v)).toLocaleString() : ''),
  },
  {
    key: 'ended_at',
    label: 'Duration',
    format: (_v, row) => {
      const r = row as Run
      if (!r.ended_at) return r.status === 'running' ? 'running…' : '—'
      const ms = new Date(r.ended_at).getTime() - new Date(r.started_at).getTime()
      const s = Math.round(ms / 1000)
      return s < 60 ? `${s}s` : `${Math.round(s / 60)}m`
    },
    widthClass: 'w-24',
  },
  { key: 'last_step', label: 'Last step', format: (v) => (v as string) ?? '—' },
]

function setStatusFilter(opt: 'all' | `${RunStatusEnum}`): void {
  runsStore.setFilter('status', opt === 'all' ? null : (opt as RunStatusEnum))
  void runsStore.refresh(projectId.value)
}

function onStatusSelect(key: string | number): void {
  setStatusFilter(String(key) as 'all' | `${RunStatusEnum}`)
}

function setKindFilter(value: string): void {
  runsStore.setFilter('kind', value === '' ? null : (value as RunKindEnum))
  void runsStore.refresh(projectId.value)
}

function setParentFilter(value: string): void {
  runsStore.setFilter('parent_run_id', value === '' ? null : Number.parseInt(value, 10))
  void runsStore.refresh(projectId.value)
}

function setSince(value: string): void {
  runsStore.setFilter('since', value === '' ? null : `${value}T00:00:00Z`)
}

function setUntil(value: string): void {
  runsStore.setFilter('until', value === '' ? null : `${value}T00:00:00Z`)
}

function onRowClick(row: Run): void {
  void router.push(`/projects/${projectId.value}/runs/${row.id}`)
}

async function loadMore(): Promise<void> {
  await runsStore.loadMore(projectId.value)
}

async function load(): Promise<void> {
  if (!projectId.value || Number.isNaN(projectId.value)) return
  runsStore.reset()
  await runsStore.refresh(projectId.value)
}

onMounted(load)
</script>

<template>
  <UiPageShell>
    <ProjectPageHeader
      :project-id="projectId"
      :title="runId !== null ? `Run #${runId}` : 'Runs'"
      :description="
        runId !== null
          ? 'Inspect run metadata, steps, heartbeats, and execution output.'
          : 'Audit run plans, skills, and tool runs with status, kind, parent, and date filters.'
      "
      :breadcrumbs="
        runId !== null
          ? [{ label: 'Runs', to: `/projects/${projectId}/runs` }, { label: `Run #${runId}` }]
          : [{ label: 'Runs' }]
      "
    >
      <template
        v-if="runId !== null"
        #actions
      >
        <UiButton
          variant="secondary"
          @click="router.push(`/projects/${projectId}/runs`)"
        >
          Back to list
        </UiButton>
      </template>
    </ProjectPageHeader>

    <UiCallout
      v-if="error"
      tone="danger"
    >
      {{ error }}
    </UiCallout>

    <RunDetail
      v-if="runId !== null"
      :key="runId"
      :run-id="runId"
      :project-id="projectId"
    />

    <div
      v-else
      class="space-y-4"
    >
      <UiPanel
        aria-label="Run filters"
        class="p-4"
      >
        <UiSegmentedControl
          :model-value="filters.status ?? 'all'"
          :options="STATUS_OPTIONS"
          label="Run status filter"
          @select="onStatusSelect"
        />

        <div class="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-[240px_180px_180px_180px]">
          <UiFormField label="Kind">
            <UiSelect
              :model-value="filters.kind ?? ''"
              :options="kindOptions"
              @update:model-value="
                (value: string | number | null) => setKindFilter(String(value ?? ''))
              "
            />
          </UiFormField>
          <UiFormField label="Parent run id">
            <UiInput
              type="number"
              min="1"
              :model-value="filters.parent_run_id ?? ''"
              @change="(value: string | number | null) => setParentFilter(String(value ?? ''))"
            />
          </UiFormField>
          <UiFormField label="Since">
            <UiInput
              type="date"
              @change="(value: string | number | null) => setSince(String(value ?? ''))"
            />
          </UiFormField>
          <UiFormField label="Until">
            <UiInput
              type="date"
              @change="(value: string | number | null) => setUntil(String(value ?? ''))"
            />
          </UiFormField>
        </div>
      </UiPanel>

      <DataTable
        :items="filteredItems"
        :columns="columns"
        :loading="loading"
        :next-cursor="nextCursor"
        aria-label="Runs"
        empty-message="No runs match the filters."
        interactive
        :sort-key="runsStore.sort.replace(/^-/, '')"
        :sort-dir="runsStore.sort.startsWith('-') ? 'desc' : 'asc'"
        @row-click="onRowClick"
        @sort="
          (col: string, dir: 'asc' | 'desc' | null) =>
            runsStore.setSort(
              `${dir === 'desc' ? '-' : ''}${col}` as 'started_at' | '-started_at' | 'id' | '-id',
            )
        "
        @load-more="loadMore"
      >
        <template #cell:status="{ row }">
          <div class="flex items-center gap-2">
            <StatusBadge
              :status="(row as Run).status"
              kind="run"
            />
          </div>
        </template>
        <template #cell:parent_run_id="{ row }">
          <UiButton
            v-if="(row as Run).parent_run_id !== null"
            variant="link"
            size="sm"
            @click.stop="router.push(`/projects/${projectId}/runs/${(row as Run).parent_run_id}`)"
          >
            #{{ (row as Run).parent_run_id }}
          </UiButton>
          <span v-else>—</span>
        </template>
      </DataTable>
    </div>
  </UiPageShell>
</template>
