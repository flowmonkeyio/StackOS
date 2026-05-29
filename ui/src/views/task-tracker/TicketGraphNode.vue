<script setup lang="ts">
import { computed } from 'vue'
import { Handle, Position } from '@vue-flow/core'

import type { TrackerVueNodeData } from '@/lib/task-tracker/graphModel'

const props = defineProps<{
  data: TrackerVueNodeData
}>()

const isOpen = computed(() => props.data.status !== 'complete' && props.data.status !== 'deferred')
const isBlocked = computed(() => isOpen.value && (props.data.blockedBy?.length ?? 0) > 0)
const statusClass = computed(() => `ticket-graph-node__status--${props.data.status}`)
</script>

<template>
  <div class="ticket-graph-node" :class="{ 'ticket-graph-node--blocked': isBlocked }">
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
      <p class="ticket-graph-node__title">{{ data.label }}</p>
      <span class="ticket-graph-node__status" :class="statusClass" :title="data.status" />
    </div>

    <p v-if="data.subtitle" class="ticket-graph-node__subtitle">
      {{ data.subtitle }}
    </p>

    <div class="ticket-graph-node__meta">
      <span>{{ data.itemKey }}</span>
      <span>{{ data.priorityKey }}</span>
      <span>{{ data.laneKey }}</span>
      <span v-if="data.assignee">{{ data.assignee }}</span>
      <span v-if="data.runPlanId">run {{ data.runPlanId }}</span>
    </div>

    <p v-if="isBlocked" class="ticket-graph-node__blocked">
      blocked by {{ data.blockedBy?.join(', ') }}
    </p>
  </div>
</template>

<style scoped>
.ticket-graph-node {
  position: relative;
  width: 236px;
  min-height: 84px;
  border: 1px solid var(--color-border-subtle);
  border-radius: 6px;
  background: var(--color-bg-surface);
  box-shadow: 0 1px 2px rgb(15 23 42 / 7%);
  padding: 9px 10px;
}

.ticket-graph-node__handle {
  width: 8px;
  height: 8px;
  border: 1px solid var(--color-border-subtle);
  background: var(--color-bg-surface);
}

.ticket-graph-node__handle--target {
  left: -1px;
}

.ticket-graph-node__handle--source {
  right: -1px;
}

.ticket-graph-node--blocked {
  border-color: color-mix(in srgb, var(--color-danger-default) 45%, var(--color-border-subtle));
}

.ticket-graph-node__top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}

.ticket-graph-node__title {
  display: -webkit-box;
  overflow: hidden;
  color: var(--color-fg-default);
  font-size: 12px;
  font-weight: 700;
  line-height: 1.25;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.ticket-graph-node__status {
  display: inline-block;
  width: 9px;
  height: 9px;
  flex: none;
  margin-top: 3px;
  border-radius: 999px;
  background: var(--color-border-strong);
}

.ticket-graph-node__status--complete {
  background: var(--color-success-default);
}

.ticket-graph-node__status--in-progress {
  background: var(--color-info-default);
}

.ticket-graph-node__status--deferred {
  background: var(--color-warning-default);
}

.ticket-graph-node__subtitle {
  overflow: hidden;
  margin-top: 4px;
  color: var(--color-fg-muted);
  font-size: 10px;
  line-height: 1.25;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ticket-graph-node__meta {
  display: flex;
  gap: 7px;
  overflow: hidden;
  margin-top: 7px;
  color: var(--color-fg-muted);
  font-family: var(--font-mono);
  font-size: 10px;
  line-height: 1;
  text-transform: uppercase;
  white-space: nowrap;
}

.ticket-graph-node__meta span {
  overflow: hidden;
  max-width: 82px;
  text-overflow: ellipsis;
}

.ticket-graph-node__blocked {
  overflow: hidden;
  margin-top: 5px;
  color: var(--color-danger-default);
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
