<script setup lang="ts">
// SchedulesTab — read-only scheduled job visibility.

import { computed, onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute } from 'vue-router'

import DataTable from '@/components/DataTable.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { UiButton, UiCallout, UiMetricCard } from '@/components/ui'
import { formatAbsoluteDateTime, formatRelativeDateTime } from '@/lib/stackos/time'
import { useSchedulesStore, type ScheduledJob } from '@/stores/schedules'
import type { DataTableColumn } from '@/components/types'

const route = useRoute()
const schedulesStore = useSchedulesStore()

const projectId = computed<number>(() => Number.parseInt(route.params.id as string, 10))
const { items, loading, error } = storeToRefs(schedulesStore)

const enabledCount = computed(() => items.value.filter((job) => job.enabled).length)

// Schedules have no created_at; the operational order is soonest next run
// first, with never-scheduled (disabled) jobs at the bottom.
const itemsByNextRun = computed(() =>
  [...items.value].sort((a, b) => {
    const ta = a.next_run_at ? Date.parse(a.next_run_at) : Number.POSITIVE_INFINITY
    const tb = b.next_run_at ? Date.parse(b.next_run_at) : Number.POSITIVE_INFINITY
    return ta - tb
  }),
)

const columns: DataTableColumn<ScheduledJob>[] = [
  { key: 'kind', label: 'Kind', cellClass: 'font-medium text-fg-strong' },
  { key: 'cron_expr', label: 'Schedule', cellClass: 'font-mono text-xs' },
  { key: 'next_run_at', label: 'Next run' },
  { key: 'last_run_at', label: 'Last run' },
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
  <section class="space-y-5">
    <div class="flex justify-end">
      <UiButton
        size="sm"
        variant="secondary"
        icon-left="refresh"
        :loading="loading"
        @click="load"
      >
        Refresh
      </UiButton>
    </div>

    <UiCallout
      v-if="error"
      tone="danger"
      title="Failed to load schedules"
    >
      {{ error }}
    </UiCallout>

    <div class="grid gap-4 md:grid-cols-3">
      <UiMetricCard
        label="Schedules"
        :value="items.length"
      />
      <UiMetricCard
        label="Enabled"
        :value="enabledCount"
      />
      <UiMetricCard
        label="Disabled"
        :value="items.length - enabledCount"
      />
    </div>

    <DataTable
      :items="itemsByNextRun"
      :columns="columns"
      :loading="loading"
      aria-label="Scheduled jobs"
      empty-message="Agent-owned schedules will appear here with next run, last run, and status."
    >
      <template #cell:next_run_at="{ value }">
        <span :title="formatAbsoluteDateTime(value ? String(value) : null)">
          {{ formatRelativeDateTime(value ? String(value) : null) }}
        </span>
      </template>
      <template #cell:last_run_at="{ value }">
        <span :title="formatAbsoluteDateTime(value ? String(value) : null)">
          {{ formatRelativeDateTime(value ? String(value) : null) }}
        </span>
      </template>
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
        <StatusBadge
          domain="step"
          :status="(row as ScheduledJob).enabled ? 'enabled' : 'disabled'"
        />
      </template>
    </DataTable>
  </section>
</template>
