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
const priorityClass = computed(() => `ticket-graph-node--priority-${props.data.priorityKey}`)
const statusLabel = computed(
  () => resolveStatus('tracker', props.data.status as TrackerStatus).label,
)
const displayedStateLabel = computed(() => (props.data.active ? 'Active now' : statusLabel.value))
const ownerLabel = computed(() => identityLabel(props.data.owner) ?? 'Unassigned')
const agentLabel = computed(() => identityLabel(props.data.agent) ?? 'Not recorded')
const sourceLabel = computed(() => humanize(props.data.sourceKind) ?? 'Tracker')

function identityLabel(value: string | null | undefined): string | null {
  if (!value?.trim()) return null
  const normalized = value.trim().toLowerCase()
  const known: Record<string, string> = {
    claude: 'Claude',
    'claude-code': 'Claude Code',
    codex: 'Codex',
    cursor: 'Cursor',
    gemini: 'Gemini',
    'gemini-cli': 'Gemini CLI',
  }
  return known[normalized] ?? humanize(value)
}

function humanize(value: string | null | undefined): string | null {
  if (!value?.trim()) return null
  return value
    .trim()
    .split(/[-_]/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}
</script>

<template>
  <div
    class="ticket-graph-node"
    :class="[
      statusClass,
      priorityClass,
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

    <span class="ticket-graph-node__priority">
      {{ data.priorityKey.toUpperCase() }}
    </span>
    <div class="ticket-graph-node__floating-state">
      <span class="ticket-graph-node__dot" />
      <span>{{ displayedStateLabel }}</span>
      <strong v-if="!data.active && data.recentlyUpdated">Updated</strong>
    </div>

    <div class="ticket-graph-node__heading">
      <p class="ticket-graph-node__title">
        {{ data.label }}
      </p>
      <span class="ticket-graph-node__key">{{ data.itemKey }}</span>
    </div>

    <p v-if="data.subtitle" class="ticket-graph-node__subtitle">
      {{ data.subtitle }}
    </p>
    <p v-else class="ticket-graph-node__subtitle ticket-graph-node__subtitle--empty">
      No additional work context recorded.
    </p>

    <dl class="ticket-graph-node__people">
      <div>
        <dt>Owner</dt>
        <dd>{{ ownerLabel }}</dd>
      </div>
      <div>
        <dt>Agent</dt>
        <dd>{{ agentLabel }}</dd>
      </div>
    </dl>

    <div class="ticket-graph-node__meta">
      <span>{{ sourceLabel }}</span>
      <span>{{ humanize(data.laneKey) }}</span>
      <span v-if="data.runPlanId">Run {{ data.runPlanId }}</span>
    </div>

    <p v-if="isBlocked" class="ticket-graph-node__blocked">
      Blocked by {{ data.blockedBy?.join(', ') }}
    </p>
  </div>
</template>

<style scoped>
.ticket-graph-node {
  --ticket-node-status: var(--color-neutral-default);
  --ticket-node-priority: var(--color-neutral-fg);

  position: relative;
  box-sizing: border-box;
  width: var(--tracker-ticket-node-width, 296px);
  min-height: var(--tracker-ticket-node-height, 176px);
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-md);
  background: var(--color-bg-surface);
  box-shadow: var(--shadow-xs);
  padding: 22px 14px 12px 16px;
}

.ticket-graph-node::before {
  position: absolute;
  inset: 0;
  border-radius: calc(var(--radius-md) - 1px) 0 0 calc(var(--radius-md) - 1px);
  background: linear-gradient(to right, var(--ticket-node-status) 4px, transparent 4px);
  content: '';
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

.ticket-graph-node--priority-p0,
.ticket-graph-node--priority-p1 {
  --ticket-node-priority: var(--color-warning-fg);
}

.ticket-graph-node--priority-p2 {
  --ticket-node-priority: var(--color-info-fg);
}

.ticket-graph-node--blocked {
  border-color: color-mix(in srgb, var(--color-danger-default) 45%, var(--color-border-default));
}

.ticket-graph-node--active {
  border-color: var(--color-accent-primary);
}

.ticket-graph-node--active .ticket-graph-node__dot {
  background: var(--color-accent-primary);
}

.ticket-graph-node--recent {
  border-color: color-mix(in srgb, var(--ticket-node-status) 62%, var(--color-border-default));
}

.ticket-graph-node__priority,
.ticket-graph-node__floating-state {
  position: absolute;
  top: -10px;
  z-index: 2;
  display: inline-flex;
  height: 20px;
  align-items: center;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-full);
  background: var(--color-bg-surface);
  box-shadow: var(--shadow-xs);
  font-size: 10px;
  line-height: 1;
}

.ticket-graph-node__priority {
  left: 14px;
  padding: 0 7px;
  color: var(--ticket-node-priority);
  font-family: var(--font-mono);
  font-weight: var(--fw-semibold);
}

.ticket-graph-node__floating-state {
  right: 12px;
  max-width: 158px;
  gap: 5px;
  padding: 0 7px;
  color: var(--color-fg-muted);
}

.ticket-graph-node__floating-state > span:not(.ticket-graph-node__dot) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ticket-graph-node__floating-state strong {
  color: var(--color-accent-primary);
  font-weight: var(--fw-semibold);
}

.ticket-graph-node__dot {
  width: 7px;
  height: 7px;
  flex: none;
  border-radius: var(--radius-full);
  background: var(--ticket-node-status);
}

.ticket-graph-node__handle {
  width: 9px;
  height: 9px;
  border: 1px solid var(--color-border-strong);
  background: var(--color-bg-surface);
}

.ticket-graph-node__handle--target {
  left: -1px;
}

.ticket-graph-node__handle--source {
  right: -1px;
}

.ticket-graph-node__heading {
  position: relative;
  display: grid;
  gap: 3px;
}

.ticket-graph-node__title {
  display: -webkit-box;
  overflow: hidden;
  min-width: 0;
  color: var(--color-fg-strong);
  font-size: var(--fs-xs);
  font-weight: var(--fw-semibold);
  line-height: 1.35;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.ticket-graph-node__key {
  overflow: hidden;
  color: var(--color-fg-subtle);
  font-family: var(--font-mono);
  font-size: 9px;
  line-height: 1.2;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ticket-graph-node__subtitle {
  display: -webkit-box;
  overflow: hidden;
  min-height: 27px;
  margin-top: 7px;
  color: var(--color-fg-muted);
  font-size: 10px;
  line-height: 1.35;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.ticket-graph-node__subtitle--empty {
  color: var(--color-fg-subtle);
}

.ticket-graph-node__people {
  position: relative;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-top: 8px;
  border-top: 1px solid var(--color-border-subtle);
  padding-top: 7px;
}

.ticket-graph-node__people div {
  min-width: 0;
}

.ticket-graph-node__people dt {
  color: var(--color-fg-subtle);
  font-size: 8px;
  font-weight: var(--fw-medium);
  letter-spacing: 0.05em;
  line-height: 1;
  text-transform: uppercase;
}

.ticket-graph-node__people dd {
  overflow: hidden;
  margin-top: 3px;
  color: var(--color-fg-default);
  font-size: 10px;
  font-weight: var(--fw-medium);
  line-height: 1.1;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ticket-graph-node__meta {
  position: relative;
  display: flex;
  gap: 8px;
  overflow: hidden;
  margin-top: 8px;
  color: var(--color-fg-subtle);
  font-size: 9px;
  line-height: 1;
  white-space: nowrap;
}

.ticket-graph-node__meta span {
  overflow: hidden;
  max-width: 92px;
  text-overflow: ellipsis;
}

.ticket-graph-node__meta span + span::before {
  margin-right: 8px;
  color: var(--color-border-strong);
  content: '·';
}

.ticket-graph-node__blocked {
  position: absolute;
  right: 12px;
  bottom: -10px;
  z-index: 4;
  overflow: hidden;
  max-width: calc(100% - 24px);
  border: 1px solid color-mix(in srgb, var(--color-danger-default) 58%, var(--color-bg-surface));
  border-radius: var(--radius-full);
  background: color-mix(in srgb, var(--color-danger-default) 22%, var(--color-bg-surface));
  box-shadow:
    0 0 0 2px var(--color-bg-surface-alt),
    var(--shadow-sm);
  padding: 4px 8px;
  color: var(--color-danger-fg);
  font-size: 9px;
  line-height: 1;
  text-overflow: ellipsis;
  white-space: nowrap;
}

:global(.tracker-node-active) .ticket-graph-node {
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--color-accent-primary) 14%, transparent);
}

:global(.tracker-node-recent) .ticket-graph-node {
  animation: tracker-recent-node 1.35s ease-out 1;
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
