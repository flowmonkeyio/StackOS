<script setup lang="ts">
import { computed } from 'vue'
import type { AgentStatsRow } from '@/lib/agentActivity'

const props = defineProps<{ rows: AgentStatsRow[]; global?: boolean }>()
const visibleRows = computed(() => props.rows.slice(0, 8))
const maximum = computed(() => Math.max(1, ...visibleRows.value.map(row => row.launch_attempts)))
</script>

<template>
  <figure aria-label="Launch attempts by recorded group">
    <figcaption class="mb-3 text-xs text-fg-muted">
      Launch attempts by recorded group<span v-if="rows.length > 8"> · First 8 groups; all groups in the table below</span>
    </figcaption>
    <ul class="space-y-3">
      <li
        v-for="(row, index) in visibleRows"
        :key="index"
        class="min-w-0"
      >
        <div class="mb-1 flex items-start justify-between gap-3 text-xs">
          <span class="min-w-0 break-words text-fg-muted">
            <span v-if="global">{{ row.project_name }} #{{ row.project_id }} · </span>
            {{ row.agent_name ?? 'Unknown agent' }} · {{ row.selected_role ?? 'Unknown role' }} · {{ row.host_key ?? 'Unknown host' }} · {{ row.model ?? 'Unknown model' }}
          </span>
          <span class="shrink-0 tabular-nums text-fg-strong">{{ row.launch_attempts.toLocaleString('en-US') }} <span class="sr-only">launch attempts</span></span>
        </div>
        <div
          aria-hidden="true"
          class="h-3 overflow-hidden rounded-sm bg-bg-sunken"
        >
          <div
            class="h-full"
            :style="{ width: `${row.launch_attempts / maximum * 100}%`, backgroundColor: 'var(--color-accent-primary)' }"
          />
        </div>
      </li>
    </ul>
  </figure>
</template>
