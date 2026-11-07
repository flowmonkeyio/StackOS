<script setup lang="ts">
import { computed } from 'vue'
import DataTable from '@/components/DataTable.vue'
import type { DataTableColumn } from '@/components/types'
import { UiCallout, UiMetricCard, UiSectionHeader, UiSkeleton } from '@/components/ui'
import { formatAbsoluteDateTime } from '@/lib/stackos/time'
import AgentActivityChart from './AgentActivityChart.vue'
import { rejectCoverage, rejectValue, usageCoverage, usageValue, type AgentStats } from '@/lib/agentActivity'

const props = defineProps<{ summary: AgentStats | null; loading: boolean; error: string | null; global?: boolean }>()
// IDs are presentation keys only. Counts and grouping remain backend-owned.
const rows = computed(() => props.summary?.rows.map((row, id) => ({ ...row, id })) ?? [])
type Row = (typeof rows.value)[number]
const columns = computed<DataTableColumn<Row>[]>(() => [
  ...(props.global ? [{ key: 'project_name' as const, label: 'Project' }] : []),
  { key: 'agent_name', label: 'Agent / role' },
  { key: 'model', label: 'Host / recorded model' },
  { key: 'launch_attempts', label: 'Launch attempts' },
  { key: 'reported_usage', label: 'Reported tokens' },
  { key: 'recorded_reviewer_rejects', label: 'Reviewer rejects' },
])
</script>

<template>
  <section
    aria-label="Agent activity"
    :aria-busy="loading"
    class="min-w-0 space-y-4"
  >
    <UiSectionHeader
      title="Agent activity"
      description="Recorded StackOS automation only. Launch attempts do not guarantee model execution; external terminal and planning sessions are excluded."
    />
    <UiCallout
      v-if="error"
      tone="warning"
    >
      {{ error }}<span v-if="summary"> Previously loaded Agent activity is shown.</span>
    </UiCallout>
    <UiSkeleton
      v-if="loading && !summary"
      shape="block"
      height="8rem"
      aria-label="Loading Agent activity"
    />
    <template v-else-if="summary">
      <div class="grid gap-3 md:grid-cols-3">
        <UiMetricCard
          label="Launch attempts"
          :value="summary.totals.launch_attempts.toLocaleString('en-US')"
          hint="Recorded invocations, including failures"
        />
        <UiMetricCard
          label="Reported tokens"
          :value="usageValue(summary.totals)"
          :hint="usageCoverage(summary.totals)"
        />
        <UiMetricCard
          label="Recorded reviewer rejects"
          :value="rejectValue(summary.totals)"
          :hint="rejectCoverage(summary.totals)"
        />
      </div>
      <p
        v-if="summary.totals.launch_attempts === 0"
        class="text-sm text-fg-muted"
      >
        No recorded agent launch attempts in this scope.
      </p>
      <template v-else>
        <AgentActivityChart
          :rows="summary.rows"
          :global="global"
        />
        <DataTable
          :items="rows"
          :columns="columns"
          aria-label="Agent activity breakdown"
          max-height="24rem"
        >
          <template #cell:project_name="{ row }">
            <span :title="row.project_slug ?? undefined">{{ row.project_name }}</span>
            <p class="text-2xs text-fg-subtle">#{{ row.project_id }} · {{ row.project_slug }}</p>
          </template>
          <template #cell:agent_name="{ row }">
            {{ row.agent_name ?? 'Unknown agent' }}
            <p class="text-2xs text-fg-subtle">{{ row.selected_role ?? 'Unknown role' }}</p>
          </template>
          <template #cell:model="{ row }">
            <p class="max-w-56 truncate" :title="row.model ?? 'Unknown model'">{{ row.model ?? 'Unknown model' }}</p>
            <p class="text-2xs text-fg-subtle">{{ row.host_key ?? 'Unknown host' }}</p>
          </template>
          <template #cell:reported_usage="{ row }">
            {{ usageValue(row) }}
            <p class="text-2xs text-fg-subtle">{{ usageCoverage(row) }}</p>
          </template>
          <template #cell:recorded_reviewer_rejects="{ row }">
            {{ rejectValue(row) }}
            <p class="text-2xs text-fg-subtle">{{ rejectCoverage(row) }}</p>
          </template>
        </DataTable>
      </template>
      <p class="text-2xs text-fg-subtle">Updated {{ formatAbsoluteDateTime(summary.as_of) }} · Model and role reflect recorded selection.</p>
    </template>
  </section>
</template>
