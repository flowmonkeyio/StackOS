<script setup lang="ts">
import { computed } from 'vue'
import { Handle, Position } from '@vue-flow/core'

import { resolveStatus } from '@/design/status'
import type { TrackerVueNodeData } from '@/lib/task-tracker/graphModel'
import { isTerminalTrackerStatus } from '@/lib/task-tracker/status'
import type { TrackerStatus } from '@/lib/task-tracker/types'

const props = defineProps<{
  data: TrackerVueNodeData
}>()

const isOpen = computed(() => !isTerminalTrackerStatus(props.data.status))
const isBlocked = computed(() => isOpen.value && (props.data.blockedBy?.length ?? 0) > 0)
const statusClass = computed(() => `ticket-graph-node--status-${props.data.status}`)
const statusLabel = computed(() => resolveStatus('tracker', props.data.status as TrackerStatus).label)
</script>

<template>
  <div
    class="ticket-graph-node"
    :class="[
      statusClass,
      {
        'ticket-graph-node--blocked': isBlocked,
        'ticket-graph-node--active': data.active,
        'ticket-graph-node--recent': data.recentlyUpdated,
      },
    ]"
  >
    <Handle
      id="in"
      type="target"
      :position="Position.Left"
      :connectable="false"
      class="ticket-graph-node__handle ticket-graph-node__handle--target"
    />
    <Handle
      id="out"
      type="source"
      :position="Position.Right"
      :connectable="false"
      class="ticket-graph-node__handle ticket-graph-node__handle--source"
    />

    <div class="ticket-graph-node__top">
      <p class="ticket-graph-node__title">
        {{ data.label }}
      </p>
      <span
        class="ticket-graph-node__dot"
        :title="data.status"
      />
    </div>

    <div class="ticket-graph-node__state">
      <span class="ticket-graph-node__status-label">{{ statusLabel }}</span>
      <span
        v-if="data.active"
        class="ticket-graph-node__active-label"
      >Active</span>
      <span
        v-else-if="data.recentlyUpdated"
        class="ticket-graph-node__active-label"
      >Updated</span>
    </div>

    <p
      v-if="data.subtitle"
      class="ticket-graph-node__subtitle"
    >
      {{ data.subtitle }}
    </p>

    <div class="ticket-graph-node__meta">
      <span class="ticket-graph-node__key">{{ data.itemKey }}</span>
      <span>{{ data.priorityKey }}</span>
      <span>{{ data.laneKey }}</span>
      <span v-if="data.assignee">{{ data.assignee }}</span>
      <span v-if="data.runPlanId">Run {{ data.runPlanId }}</span>
    </div>

    <p
      v-if="isBlocked"
      class="ticket-graph-node__blocked"
    >
      Blocked by {{ data.blockedBy?.join(', ') }}
    </p>
  </div>
</template>

<style scoped>
.ticket-graph-node {
  --ticket-node-status: var(--color-neutral-default);

  position: relative;
  box-sizing: border-box;
  width: 236px;
  min-height: 84px;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-md);
  background: var(--color-bg-surface);
  box-shadow: var(--shadow-xs);
  padding: 9px 10px 9px 14px;
}

/*
 * Status accent strip. Painted as a full-size underlay whose own radius
 * matches the card's inner curve, so the strip is clipped along the
 * rounded corners instead of poking past them (and never overlaps the
 * border, which selection/blocked states recolor).
 */
.ticket-graph-node::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: calc(var(--radius-md) - 1px) 0 0 calc(var(--radius-md) - 1px);
  background: linear-gradient(to right, var(--ticket-node-status) 3px, transparent 3px);
}

.ticket-graph-node:hover {
  box-shadow: var(--shadow-sm);
}

.ticket-graph-node--status-complete {
  --ticket-node-status: var(--color-success-default);
}

.ticket-graph-node--status-in-progress {
  --ticket-node-status: var(--color-info-default);
}

.ticket-graph-node--status-deferred {
  --ticket-node-status: var(--color-warning-default);
}

.ticket-graph-node--status-aborted,
.ticket-graph-node--status-failed {
  --ticket-node-status: var(--color-danger-default);
}

.ticket-graph-node--blocked {
  border-color: color-mix(in srgb, var(--color-danger-default) 45%, var(--color-border-default));
}

.ticket-graph-node--active {
  border-color: var(--color-accent-primary);
}

.ticket-graph-node--recent {
  border-color: color-mix(in srgb, var(--ticket-node-status) 62%, var(--color-border-default));
}

.ticket-graph-node__handle {
  width: 8px;
  height: 8px;
  border: 1px solid var(--color-border-strong);
  background: var(--color-bg-surface);
}

.ticket-graph-node__handle--target {
  left: -1px;
}

.ticket-graph-node__handle--source {
  right: -1px;
}

.ticket-graph-node__top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.ticket-graph-node__title {
  overflow: hidden;
  min-width: 0;
  color: var(--color-fg-strong);
  font-size: var(--fs-2xs);
  font-weight: var(--fw-medium);
  line-height: var(--lh-2xs);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ticket-graph-node__dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  flex: none;
  border-radius: var(--radius-full);
  background: var(--ticket-node-status);
}

.ticket-graph-node__state {
  display: flex;
  align-items: center;
  gap: 6px;
  overflow: hidden;
  margin-top: 5px;
  white-space: nowrap;
}

.ticket-graph-node__status-label,
.ticket-graph-node__active-label {
  overflow: hidden;
  max-width: 98px;
  border-radius: var(--radius-sm);
  padding: 2px 5px;
  font-size: 10px;
  font-weight: var(--fw-semibold);
  line-height: 1;
  text-overflow: ellipsis;
}

.ticket-graph-node__status-label {
  background: color-mix(in srgb, var(--ticket-node-status) 14%, var(--color-bg-surface));
  color: color-mix(in srgb, var(--ticket-node-status) 82%, var(--color-fg-strong));
}

.ticket-graph-node__active-label {
  background: color-mix(in srgb, var(--color-accent-primary) 12%, var(--color-bg-surface));
  color: var(--color-accent-primary);
}

.ticket-graph-node__subtitle {
  overflow: hidden;
  margin-top: 3px;
  color: var(--color-fg-muted);
  font-size: var(--fs-2xs);
  line-height: var(--lh-2xs);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ticket-graph-node__meta {
  display: flex;
  gap: 7px;
  overflow: hidden;
  margin-top: 8px;
  color: var(--color-fg-subtle);
  font-size: var(--fs-2xs);
  line-height: 1;
  white-space: nowrap;
}

.ticket-graph-node__meta span {
  overflow: hidden;
  max-width: 82px;
  text-overflow: ellipsis;
}

.ticket-graph-node__key {
  font-family: var(--font-mono);
}

.ticket-graph-node__blocked {
  overflow: hidden;
  margin-top: 5px;
  color: var(--color-danger-fg);
  font-size: var(--fs-2xs);
  line-height: var(--lh-2xs);
  text-overflow: ellipsis;
  white-space: nowrap;
}

:global(.tracker-node-active) .ticket-graph-node {
  animation: tracker-active-node 1.8s ease-in-out infinite;
}

:global(.tracker-node-recent) .ticket-graph-node {
  animation: tracker-recent-node 1.35s ease-out 1;
}

@keyframes tracker-active-node {
  0%,
  100% {
    box-shadow: 0 0 0 0 color-mix(in srgb, var(--color-accent-primary) 0%, transparent);
  }

  50% {
    box-shadow: 0 0 0 4px color-mix(in srgb, var(--color-accent-primary) 18%, transparent);
  }
}

@keyframes tracker-recent-node {
  0% {
    box-shadow: 0 0 0 0 color-mix(in srgb, var(--ticket-node-status) 0%, transparent);
  }

  34% {
    box-shadow: 0 0 0 5px color-mix(in srgb, var(--ticket-node-status) 20%, transparent);
  }

  100% {
    box-shadow: var(--shadow-xs);
  }
}

@media (prefers-reduced-motion: reduce) {
  :global(.tracker-node-active) .ticket-graph-node,
  :global(.tracker-node-recent) .ticket-graph-node {
    animation: none;
  }
}
</style>
