<script setup lang="ts">
// SchedulesTab — read-only scheduled job visibility.

import { computed, onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute } from 'vue-router'

import DataTable from '@/components/DataTable.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { UiBadge, UiButton, UiCallout, UiSectionHeader } from '@/components/ui'
import { useSchedulesStore, type ScheduledJob } from '@/stores/schedules'
import type { DataTableColumn } from '@/components/types'

const route = useRoute()
const schedulesStore = useSchedulesStore()

const projectId = computed<number>(() => Number.parseInt(route.params.id as string, 10))
const { items, loading, error } = storeToRefs(schedulesStore)

const enabledCount = computed(() => items.value.filter((job) => job.enabled).length)

const columns: DataTableColumn<ScheduledJob>[] = [
  { key: 'kind', label: 'Kind', cellClass: 'font-mono text-sm' },
  { key: 'cron_expr', label: 'Cron', cellClass: 'font-mono text-sm' },
  {
    key: 'next_run_at',
    label: 'Next run',
    format: (value) => (value ? new Date(String(value)).toLocaleString() : '-'),
  },
  {
    key: 'last_run_at',
    label: 'Last run',
    format: (value) => (value ? new Date(String(value)).toLocaleString() : '-'),
  },
  { key: 'last_run_status', label: 'Last status', widthClass: 'w-28' },
  { key: 'enabled', label: 'State', widthClass: 'w-24' },
]

async function load(): Promise<void> {
  if (!projectId.value || Number.isNaN(projectId.value)) return
  await schedulesStore.refresh(projectId.value)
}

onMounted(load)
</script>

<template>
  <section class="space-y-6">
    <UiSectionHeader
      title="Scheduled jobs"
      description="Read-only schedule inventory for recurring agent-owned maintenance."
    >
      <template #actions>
        <UiButton
          size="sm"
          variant="secondary"
          :disabled="loading"
          @click="load"
        >
          {{ loading ? 'Refreshing...' : 'Refresh' }}
        </UiButton>
      </template>
    </UiSectionHeader>

    <UiCallout
      v-if="error"
      tone="danger"
      title="Failed to load schedules"
    >
      {{ error }}
    </UiCallout>

    <div class="grid gap-3 md:grid-cols-3">
      <div class="rounded-md border border-default bg-bg-surface p-3 shadow-xs">
        <p class="text-[11px] font-semibold uppercase tracking-wide text-fg-subtle">
          Schedules
        </p>
        <p class="mt-2 text-sm font-semibold text-fg-strong">
          {{ items.length }}
        </p>
        <p class="mt-1 text-xs text-fg-muted">
          Recurring jobs in the project.
        </p>
      </div>
      <div class="rounded-md border border-default bg-bg-surface p-3 shadow-xs">
        <p class="text-[11px] font-semibold uppercase tracking-wide text-fg-subtle">
          Enabled
        </p>
        <p class="mt-2 text-sm font-semibold text-fg-strong">
          {{ enabledCount }}
        </p>
        <p class="mt-1 text-xs text-fg-muted">
          Jobs currently allowed to run.
        </p>
      </div>
      <div class="rounded-md border border-default bg-bg-surface p-3 shadow-xs">
        <p class="text-[11px] font-semibold uppercase tracking-wide text-fg-subtle">
          Disabled
        </p>
        <p class="mt-2 text-sm font-semibold text-fg-strong">
          {{ items.length - enabledCount }}
        </p>
        <p class="mt-1 text-xs text-fg-muted">
          Paused jobs retained for audit.
        </p>
      </div>
    </div>

    <div
      v-if="!loading && items.length === 0"
      class="rounded-md border border-dashed border-subtle bg-bg-surface px-4 py-8 text-center"
    >
      <p class="text-sm font-semibold text-fg-strong">
        No scheduled jobs
      </p>
      <p class="mt-1 text-sm text-fg-muted">
        Agent-owned schedules will appear here with next run, last run, and status.
      </p>
    </div>

    <DataTable
      v-else
      :items="items"
      :columns="columns"
      :loading="loading"
      aria-label="Scheduled jobs"
      empty-message="No scheduled jobs yet."
    >
      <template #cell:last_run_status="{ row }">
        <StatusBadge
          v-if="(row as ScheduledJob).last_run_status"
          :status="(row as ScheduledJob).last_run_status as string"
          kind="job"
          :small="true"
        />
        <span v-else>-</span>
      </template>
      <template #cell:enabled="{ row }">
        <UiBadge :tone="(row as ScheduledJob).enabled ? 'success' : 'warning'">
          {{ (row as ScheduledJob).enabled ? 'Enabled' : 'Disabled' }}
        </UiBadge>
      </template>
    </DataTable>
  </section>
</template>
