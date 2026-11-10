<script setup lang="ts">
import { computed } from 'vue'
import { Handle, Position } from '@vue-flow/core'
import { UiBadge } from '@/components/ui'
import AgentUsageSummary from '@/components/domain/AgentUsageSummary.vue'
import ActiveExecutionLine from '@/components/domain/ActiveExecutionLine.vue'
import { executionStatus } from '@/lib/task-tracker/execution'
import type { TrackerVueNodeData } from '@/lib/task-tracker/graphModel'
import { isTerminalTrackerStatus } from '@/lib/task-tracker/status'

const props = defineProps<{ data: TrackerVueNodeData }>()
const isBlocked = computed(() => !isTerminalTrackerStatus(props.data.status) && (props.data.blockedBy?.length ?? 0) > 0)
const state = computed(() => executionStatus(props.data.status, props.data.activeExecution, props.data.deliveryStopped
  ? { status: 'stopped', ticketKey: props.data.itemKey, reason: props.data.deliveryStopReason ?? null } : null))
const blocker = computed(() => `Blocked by: ${props.data.blockedBy?.join(', ')}`)
</script>

<template>
  <div
    class="ticket-graph-node"
    :style="{ '--ticket-status-color': `var(--color-${isBlocked ? 'warning' : state.tone}-default)` }"
    :class="[
      `ticket-graph-node--status-${data.status}`,
      { 'ticket-graph-node--blocked': isBlocked, 'ticket-graph-node--active': !!data.activeExecution, 'ticket-graph-node--recent': data.recentlyUpdated },
    ]"
  >
    <Handle
      id="in"
      type="target"
      :position="Position.Left"
      :connectable="false"
      class="ticket-graph-node__handle"
    />
    <Handle
      id="out"
      type="source"
      :position="Position.Right"
      :connectable="false"
      class="ticket-graph-node__handle"
    />
    <div class="ticket-graph-node__top">
      <span
        class="ticket-graph-node__key"
        :title="data.itemKey"
      >{{ typeof data.raw.id === 'number' ? `#${data.raw.id}` : data.itemKey }}</span>
      <UiBadge
        tone="info"
        variant="outline"
      >
        {{ data.priorityKey.toUpperCase() }}
      </UiBadge>
      <UiBadge
        class="ticket-graph-node__floating-state"
        :tone="state.tone"
        :dot="true"
        variant="outline"
        :title="data.deliveryStopped ? data.deliveryStopReason ?? undefined : data.recentlyUpdated ? 'Updated' : undefined"
      >
        {{ state.label }}
      </UiBadge>
    </div>
    <p
      class="ticket-graph-node__title"
      :title="data.label"
    >
      {{ data.label }}
    </p>
    <p
      class="ticket-graph-node__subtitle"
      :title="data.deliveryStopped ? data.deliveryStopReason ?? undefined : data.subtitle"
    >
      {{ data.deliveryStopped ? data.deliveryStopReason : data.subtitle }}
    </p>
    <AgentUsageSummary
      v-if="data.taskAutomated"
      class="ticket-graph-node__usage"
      :metrics="data.agentStats"
      :active="!!data.activeExecution"
      compact
      title="Own ticket usage"
    />
    <div class="ticket-graph-node__footer">
      <ActiveExecutionLine
        :item="data.lifecycle"
        :execution="data.activeExecution"
        :last-execution="data.taskAutomated ? data.agentStats?.last_execution : null"
        :owner="data.owner"
        class="ticket-graph-node__execution"
        focusable
      />
    </div>
    <p
      v-if="isBlocked"
      class="ticket-graph-node__blocked"
      :title="blocker"
      tabindex="0"
    >
      {{ blocker }}
    </p>
  </div>
</template>

<style scoped>
.ticket-graph-node { position: relative; box-sizing: border-box; width: var(--tracker-ticket-node-width, 328px); height: var(--tracker-ticket-node-height, 224px); padding: 12px 14px 54px; border: 1px solid var(--color-border-default); border-radius: var(--radius-lg); background: var(--color-bg-surface); box-shadow: var(--shadow-xs); }
.ticket-graph-node::before { content: ''; position: absolute; inset: 0 auto 0 0; width: 3px; border-radius: var(--radius-lg) 0 0 var(--radius-lg); background: var(--ticket-status-color); pointer-events: none; }
.ticket-graph-node::after { content: ''; position: absolute; inset: -1px; z-index: 1; border: 1px solid var(--color-border-default); border-radius: inherit; pointer-events: none; }
.ticket-graph-node--active::after { border-color: var(--color-border-focus); }
.ticket-graph-node:hover { box-shadow: var(--shadow-sm); }
.ticket-graph-node--active { border-color: var(--color-border-focus); }
.ticket-graph-node__top { display: flex; height: 20px; min-width: 0; align-items: center; gap: 6px; }
.ticket-graph-node__key { min-width: 0; max-width: 100px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--color-fg-muted); font-size: 10px; }
.ticket-graph-node__floating-state { flex: none; margin-left: auto; }
.ticket-graph-node__handle { z-index: 2; width: 9px; height: 9px; border: 1px solid var(--color-border-strong); background: var(--color-bg-surface); }
.ticket-graph-node__title { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden; overflow-wrap: anywhere; height: 40px; margin-top: 6px; color: var(--color-fg-strong); font-size: 14px; font-weight: var(--fw-semibold); line-height: 20px; }
.ticket-graph-node__subtitle { height: 16px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--color-fg-muted); font-size: 11px; line-height: 16px; }
.ticket-graph-node__usage { margin-top: 8px; }
.ticket-graph-node__footer { position: absolute; inset: auto 0 0; height: 56px; display: flex; align-items: center; border-top: 1px solid var(--color-border-default); padding: 0 14px; }
.ticket-graph-node__execution { width: 100%; }
.ticket-graph-node__blocked { position: absolute; bottom: -10px; left: 20px; right: 20px; z-index: 4; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; width: fit-content; max-width: calc(100% - 40px); margin: 0 auto; padding: 3px 8px; border: 1px solid var(--color-warning-default); border-radius: var(--radius-full); background: var(--color-warning-subtle); color: var(--color-warning-fg); font-size: 10px; line-height: 14px; }
.ticket-graph-node__blocked:focus-visible { outline: 2px solid var(--color-border-focus); outline-offset: 2px; }
:global(.tracker-node-active) .ticket-graph-node { box-shadow: 0 0 0 2px var(--color-accent-subtle); }
</style>
