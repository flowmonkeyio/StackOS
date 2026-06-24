<script setup lang="ts">
import { computed } from 'vue'

import StatusBadge from '@/components/StatusBadge.vue'
import { UiBadge } from '@/components/ui'
import { resolveStatus, type Tone } from '@/design/status'
import type { TrackerVueNodeData } from '@/lib/task-tracker/graphModel'

const props = defineProps<{
  data: TrackerVueNodeData
}>()

const initials = computed(() =>
  props.data.label
    .split(/\s+/)
    .slice(0, 2)
    .map((item) => item[0]?.toUpperCase() ?? '')
    .join(''),
)

// Status accent strip color derives from the resolved tone token, so the
// status→color mapping stays single-sourced in design/status.ts.
const TONE_VAR: Record<Tone, string> = {
  neutral: 'var(--color-neutral-default)',
  info: 'var(--color-info-default)',
  success: 'var(--color-success-default)',
  warning: 'var(--color-warning-default)',
  danger: 'var(--color-danger-default)',
}

const statusColor = computed(() => TONE_VAR[resolveStatus('tracker', props.data.status).tone])
</script>

<template>
  <div
    class="task-graph-node"
    :style="{ '--task-node-status': statusColor }"
  >
    <div class="flex items-start gap-3">
      <div class="task-graph-node__mark">
        {{ initials }}
      </div>
      <div class="min-w-0 flex-1">
        <div class="flex flex-wrap items-center gap-2">
          <p class="truncate text-sm font-semibold text-fg-strong">
            {{ data.label }}
          </p>
          <StatusBadge
            domain="tracker"
            :status="data.status"
          />
        </div>
        <p
          v-if="data.subtitle"
          class="mt-1 line-clamp-2 text-xs text-fg-muted"
        >
          {{ data.subtitle }}
        </p>
      </div>
    </div>
    <div class="mt-3 flex flex-wrap gap-2">
      <UiBadge
        tone="neutral"
        variant="outline"
        size="sm"
      >
        {{ data.priorityKey }}
      </UiBadge>
      <UiBadge
        tone="neutral"
        variant="outline"
        size="sm"
      >
        {{ data.laneKey }}
      </UiBadge>
    </div>
  </div>
</template>

<style scoped>
.task-graph-node {
  --task-node-status: var(--color-neutral-default);

  position: relative;
  box-sizing: border-box;
  width: 100%;
  min-width: 312px;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-md);
  background: var(--color-bg-surface);
  box-shadow: var(--shadow-xs);
  padding: 14px 14px 14px 18px;
}

/*
 * Status accent strip. Painted as a full-size underlay whose own radius
 * matches the card's inner curve, so the strip is clipped along the
 * rounded corners instead of poking past them (and never overlaps the
 * border, which selection states recolor).
 */
.task-graph-node::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: calc(var(--radius-md) - 1px) 0 0 calc(var(--radius-md) - 1px);
  background: linear-gradient(to right, var(--task-node-status) 3px, transparent 3px);
}

.task-graph-node:hover {
  box-shadow: var(--shadow-sm);
}

.task-graph-node__mark {
  display: grid;
  width: 34px;
  height: 34px;
  flex: none;
  place-items: center;
  border-radius: var(--radius-md);
  background: var(--color-bg-sunken);
  color: var(--color-fg-muted);
  font-size: var(--fs-2xs);
  font-weight: var(--fw-semibold);
}
</style>
