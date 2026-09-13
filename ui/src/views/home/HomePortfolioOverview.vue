<script setup lang="ts">
import { UiCallout, UiCard, UiSectionHeader, UiSegmentedControl, UiSkeleton } from '@/components/ui'
import { formatAbsoluteDateTime } from '@/lib/stackos/time'
import type { TrackerStatus } from '@/lib/task-tracker/types'
import TicketStatusChart from './TicketStatusChart.vue'
import type { TicketCountSummary } from './ticketOverview'

defineProps<{ summary: TicketCountSummary | null; visibility: boolean | null; selectedStatus: TrackerStatus | null; loading: boolean; error: string | null }>()
defineEmits<{ visibility: [value: string | number]; select: [status: TrackerStatus] }>()
</script>

<template>
  <UiCard
    section
    aria-label="Tickets by status"
  >
    <UiSectionHeader
      title="Tickets by status"
      :description="summary ? `Current snapshot · ${summary.total_count} ${summary.total_count === 1 ? 'ticket' : 'tickets'}` : 'Current ticket status across projects'"
    >
      <template #actions>
        <span class="text-xs text-fg-muted">Projects</span>
        <UiSegmentedControl
          :model-value="visibility === null ? 'all' : visibility ? 'active' : 'archived'"
          :options="[{ key: 'active', label: 'Active' }, { key: 'archived', label: 'Archived' }, { key: 'all', label: 'All' }]"
          label="Project visibility"
          @update:model-value="$emit('visibility', $event)"
        />
      </template>
    </UiSectionHeader>
    <UiCallout
      v-if="error"
      tone="warning"
      class="mt-3"
    >
      {{ error }}<span v-if="summary"> Previously loaded counts are shown.</span>
    </UiCallout>
    <UiSkeleton
      v-if="loading && !summary"
      shape="block"
      height="8rem"
      class="mt-3"
    />
    <div
      v-else-if="summary"
      class="mt-3 space-y-3"
    >
      <TicketStatusChart
        :counts="summary.ticket_counts"
        :total="summary.total_count"
        :selected-status="selectedStatus"
        @select="$emit('select', $event)"
      />
      <p class="border-t border-subtle pt-3 text-2xs text-fg-subtle">
        {{ visibility === null ? 'All projects' : visibility ? 'Active projects' : 'Archived projects' }} · Updated {{ formatAbsoluteDateTime(summary.as_of) }}
      </p>
    </div>
  </UiCard>
</template>
