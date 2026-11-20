<script setup lang="ts">
import { computed, ref } from 'vue'
import { storeToRefs } from 'pinia'
import DataTable from '@/components/DataTable.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import AgentUsageSummary from '@/components/domain/AgentUsageSummary.vue'
import WorkTime from '@/components/domain/WorkTime.vue'
import RunAbortAction from '@/components/domain/RunAbortAction.vue'
import RunAbortDialog from '@/components/domain/RunAbortDialog.vue'
import { UiCallout, UiIcon, UiIconButton, UiInput } from '@/components/ui'
import type { DataTableColumn } from '@/components/types'
import { formatAbsoluteDateTime, formatRelativeDateTime, formatDurationMinutes, formatDurationBetween } from '@/lib/stackos/time'
import { useSchedulesStore } from '@/stores/schedules'
import { formatTokens } from '@/lib/agentActivity'

defineProps<{ projectId: number }>()
defineEmits<{ refresh: [] }>()
const store = useSchedulesStore()
const { runs, runsLoading, runsError, nextCursor } = storeToRefs(store)
const search = ref('')
const rows = computed(() => runs.value.filter(row => `${row.schedule_name} ${row.workflow_title ?? row.template_key ?? 'Prompt'} ${row.summary ?? ''} ${row.error ?? ''}`.toLowerCase().includes(search.value.trim().toLowerCase())).map(row => ({ ...row, id: row.request_id })))
type Row = typeof rows.value[number]
const nativeUsageFields = [{ key: 'input', label: 'Input' }, { key: 'cache_read', label: 'Cache' }, { key: 'output', label: 'Output' }] as const
function nativeUsage(row: Row, key: string): number | null {
  const live = row.native_execution && row.active_execution?.live_usage?.usage
  const usage = row.usage ?? live
  if (!usage || typeof usage !== 'object' || !('status' in usage) || usage.status !== 'measured' || !('measured' in usage)) return null
  const value = (usage.measured as Record<string, unknown> | null)?.[key]
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null
}
function duration(row: Row): string {
  if (row.duration_seconds != null) return row.duration_seconds === 0 ? '0s' : formatDurationMinutes(row.duration_seconds / 60)
  if (row.ended_at || row.active_execution) return formatDurationBetween(row.started_at, row.ended_at)
  return '—'
}
const columns: DataTableColumn<Row>[] = [
  { key: 'schedule_name', label: 'Run', widthClass: 'w-[18%]' },
  { key: 'started_at', label: 'Started', widthClass: 'w-[11%]' },
  { key: 'status', label: 'Status', widthClass: 'w-[11%]' },
  { key: 'duration_seconds', label: 'Work time', widthClass: 'w-[8%]' },
  { key: 'summary', label: 'Result', widthClass: 'w-[18%]' },
  { key: 'agent_stats', label: 'Usage', widthClass: 'w-[24%]' },
  { key: 'task_key', label: '', widthClass: 'w-[10%]' },
]
</script>

<template>
  <div
    role="tabpanel"
    aria-labelledby="cs-tab-runs"
    class="space-y-4"
  >
    <RunAbortDialog />
    <div class="flex min-h-8 items-center justify-between gap-4">
      <p class="text-sm text-fg-muted">
        Runs only while StackOS is open. Missed runs are skipped.
      </p>
      <div class="flex shrink-0 items-center gap-2">
        <UiIconButton
          aria-label="Refresh runs"
          :disabled="runsLoading"
          @click="$emit('refresh')"
        >
          <UiIcon
            name="refresh"
            class="h-4 w-4"
          />
        </UiIconButton>
        <UiInput
          v-model="search"
          type="search"
          aria-label="Search runs"
          placeholder="Search runs"
          class="w-64"
        >
          <template #prefix>
            <UiIcon
              name="search"
              class="h-4 w-4"
            />
          </template>
        </UiInput>
      </div>
    </div>
    <UiCallout
      v-if="runsError"
      tone="danger"
      title="Failed to load runs"
    >
      {{ runsError }}
    </UiCallout>
    <DataTable
      :items="rows"
      :columns="columns"
      :loading="runsLoading"
      :next-cursor="nextCursor"
      aria-label="Automation runs"
      max-height="calc(100dvh - 240px)"
      :empty-message="runsError ? 'Run history unavailable. Refresh to retry.' : search ? 'No matching runs in loaded history.' : 'No runs yet. Actual scheduled executions will appear here.'"
      @load-more="store.refreshRuns(projectId, true)"
    >
      <template #cell:schedule_name="{ row }">
        <div
          class="truncate font-medium text-fg-strong"
          :title="row.schedule_name"
        >
          {{ row.schedule_name }}
        </div>
        <div
          class="truncate text-xs text-fg-muted"
          :title="row.workflow_title ?? row.template_key ?? undefined"
        >
          {{ row.workflow_title ?? row.template_key ?? 'Prompt' }}
        </div>
        <div
          class="truncate text-xs text-fg-muted"
          :title="row.native_execution?.thread_id ?? undefined"
        >
          Request #{{ row.request_id }}{{ row.native_execution?.thread_id ? ' · Session recorded' : '' }}
        </div>
      </template>
      <template #cell:started_at="{ row }">
        <span :title="formatAbsoluteDateTime(row.started_at)">{{ formatRelativeDateTime(row.started_at) }}</span>
      </template>
      <template #cell:status="{ row }">
        <StatusBadge
          :domain="row.native_execution || !row.run_plan_id ? 'agentRequest' : 'run'"
          :status="row.status"
        />
      </template>
      <template #cell:duration_seconds="{ row }">
        <span
          v-if="row.native_execution"
          class="tabular-nums"
        >{{ duration(row) }}</span>
        <WorkTime
          v-else
          :item="row"
          :execution="row.active_execution && 'ticket_key' in row.active_execution ? row.active_execution : null"
          class="scheduled-run-work-time"
        />
      </template>
      <template #cell:summary="{ row }">
        <div
          class="truncate text-xs"
          :title="row.error ?? row.summary ?? undefined"
          :class="row.error ? 'text-danger-fg' : 'text-fg-muted'"
        >
          {{ row.error ?? row.summary ?? row.outcome_status ?? 'Not reported' }}
        </div>
      </template>
      <template #cell:agent_stats="{ row }">
        <dl
          v-if="row.native_execution || !row.agent_stats"
          class="grid grid-cols-3 gap-2 text-xs tabular-nums"
          aria-label="Main thread token usage"
          title="Measured native main-thread tokens. Input includes cached tokens; linked task usage is separate."
        >
          <div
            v-for="field in nativeUsageFields"
            :key="field.key"
            class="min-w-0"
          >
            <dt class="text-fg-muted">
              {{ field.label }}
            </dt>
            <dd
              class="truncate"
              :title="`${field.label}: ${nativeUsage(row, field.key) ?? 'Not reported'}`"
            >
              {{ formatTokens(nativeUsage(row, field.key)) }}
            </dd>
          </div>
        </dl>
        <AgentUsageSummary
          v-else
          :metrics="row.agent_stats"
          compact
          inline
        />
      </template>
      <template #cell:task_key="{ row }">
        <div class="flex items-center justify-end gap-2">
          <RouterLink
            v-if="row.task_key"
            :to="{ path: `/projects/${projectId}/tasks`, query: { view: 'graph', task: row.task_key } }"
            class="focus-ring inline-flex items-center gap-1 whitespace-nowrap rounded-sm text-xs font-medium text-accent-fg"
            :aria-label="`View ${row.schedule_name} in Work`"
          >
            View in Work
            <UiIcon
              name="arrow-right"
              class="h-3 w-3"
            />
          </RouterLink>
          <RunAbortAction
            v-if="row.run_plan_id && !row.native_execution"
            :target="{ projectId, runPlanId: row.run_plan_id, title: row.schedule_name, status: row.status, automated: true }"
            @refresh="$emit('refresh')"
          />
        </div>
      </template>
    </DataTable>
  </div>
</template>
