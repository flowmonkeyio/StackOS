<script setup lang="ts">
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import { VueFlow } from '@vue-flow/core'
import type { EdgeMouseEvent, NodeMouseEvent } from '@vue-flow/core'

import { UiButton, UiPanel } from '@/components/ui'
import type { TrackerFlowModel } from '@/lib/task-tracker/graphModel'
import type { TrackerStatus, TrackerTicket } from '@/lib/task-tracker/types'

import TicketGraphNode from './TicketGraphNode.vue'
import TrackerStatusBadge from './TrackerStatusBadge.vue'
import type { GraphBlockFilter } from './viewTypes'

import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import '@vue-flow/minimap/dist/style.css'

defineProps<{
  flow: TrackerFlowModel
  flowRenderKey: string
  graphFitOnInit: boolean
  activeTaskTitle: string
  activeTaskAvailable: boolean
  ticketStatLabel: string
  edgeStatLabel: string
  statusRows: Array<{ key: TrackerStatus; label: string; count: number }>
  statusFilters: TrackerStatus[]
  blockRows: Array<{ key: GraphBlockFilter; label: string; count: number }>
  blockFilters: GraphBlockFilter[]
  filtersActive: boolean
  selectionVisible: boolean
  selectionLabel: string
  selectedTicket: TrackerTicket | null
  selectedEdgeLabel: string | null
  selectionStats: string[]
}>()

defineEmits<{
  (e: 'toggleStatus', value: TrackerStatus): void
  (e: 'toggleBlock', value: GraphBlockFilter): void
  (e: 'clearFilters'): void
  (e: 'openTaskDetail'): void
  (e: 'nodeClick', value: NodeMouseEvent): void
  (e: 'edgeClick', value: EdgeMouseEvent): void
  (e: 'paneClick', value: MouseEvent): void
  (e: 'graphCanvasClick', value: MouseEvent): void
  (e: 'openSelectedDetail'): void
  (e: 'clearGraphFocus'): void
}>()
</script>

<template>
  <UiPanel :padded="false" class="tracker-flow-shell">
    <div class="tracker-flow-shell__bar">
      <div>
        <p class="tracker-flow-shell__eyebrow">Dependency map</p>
        <p class="tracker-flow-shell__title">{{ activeTaskTitle }}</p>
      </div>
      <div class="tracker-flow-shell__right">
        <div class="tracker-flow-shell__stats">
          <span>{{ ticketStatLabel }}</span>
          <span>{{ edgeStatLabel }}</span>
        </div>
        <UiButton
          class="tracker-task-details-button"
          variant="secondary"
          size="sm"
          icon-left="file-text"
          :disabled="!activeTaskAvailable"
          aria-label="Open task details"
          @click="$emit('openTaskDetail')"
        >
          Task details
        </UiButton>
      </div>
    </div>

    <div class="tracker-graph-controls">
      <div class="tracker-graph-filter-group">
        <span class="tracker-graph-filter-group__label">Status</span>
        <button
          v-for="item in statusRows"
          :key="item.key"
          type="button"
          class="tracker-graph-filter"
          :class="{ 'tracker-graph-filter--active': statusFilters.includes(item.key) }"
          @click="$emit('toggleStatus', item.key)"
        >
          <span>{{ item.label }}</span>
          <strong>{{ item.count }}</strong>
        </button>
      </div>
      <div class="tracker-graph-filter-group">
        <span class="tracker-graph-filter-group__label">Block</span>
        <button
          v-for="item in blockRows"
          :key="item.key"
          type="button"
          class="tracker-graph-filter"
          :class="[
            `tracker-graph-filter--${item.key}`,
            { 'tracker-graph-filter--active': blockFilters.includes(item.key) },
          ]"
          @click="$emit('toggleBlock', item.key)"
        >
          <span>{{ item.label }}</span>
          <strong>{{ item.count }}</strong>
        </button>
      </div>
      <div class="tracker-graph-controls__tail">
        <button
          v-if="filtersActive"
          type="button"
          class="tracker-graph-clear"
          @click="$emit('clearFilters')"
        >
          Clear graph
        </button>
      </div>
    </div>

    <div class="tracker-flow-frame" @click.capture="$emit('graphCanvasClick', $event)">
      <VueFlow
        :key="flowRenderKey"
        class="tracker-flow"
        :nodes="flow.nodes"
        :edges="flow.edges"
        :default-viewport="{ x: 32, y: 32, zoom: 0.72 }"
        :fit-view-on-init="graphFitOnInit"
        :min-zoom="0.12"
        :max-zoom="1.5"
        pan-on-scroll
        @node-click="$emit('nodeClick', $event)"
        @edge-click="$emit('edgeClick', $event)"
        @pane-click="$emit('paneClick', $event)"
      >
        <template #node-tracker-ticket="props">
          <TicketGraphNode v-bind="props" />
        </template>
        <Background pattern-color="var(--color-border-subtle)" />
        <MiniMap pannable zoomable />
        <Controls />
        <div
          v-if="selectionVisible"
          class="tracker-graph-selection"
          @pointerdown.stop
          @mousedown.stop
        >
          <div class="tracker-graph-selection__main">
            <div class="tracker-graph-selection__title-row">
              <p class="tracker-graph-selection__eyebrow">{{ selectionLabel }}</p>
              <p class="tracker-graph-selection__title">
                {{ selectedTicket?.title ?? 'Dependency context' }}
              </p>
              <TrackerStatusBadge v-if="selectedTicket" :status="selectedTicket.status" />
            </div>
            <div class="tracker-graph-selection__meta">
              <span v-if="selectedTicket">{{ selectedTicket.key }}</span>
              <span v-if="selectedEdgeLabel">{{ selectedEdgeLabel }}</span>
              <span v-for="stat in selectionStats" :key="stat">{{ stat }}</span>
              <span v-if="selectedTicket?.assignee">owner {{ selectedTicket.assignee }}</span>
              <span v-if="selectedTicket?.run_plan_id">run {{ selectedTicket.run_plan_id }}</span>
            </div>
          </div>
          <div class="tracker-graph-selection__actions">
            <UiButton
              v-if="selectedTicket"
              variant="secondary"
              size="sm"
              @click.stop="$emit('openSelectedDetail')"
            >
              Details
            </UiButton>
            <UiButton variant="ghost" size="sm" @click.stop="$emit('clearGraphFocus')">
              Clear
            </UiButton>
          </div>
        </div>
      </VueFlow>
    </div>
  </UiPanel>
</template>

<style scoped>
.tracker-flow-shell {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-height: 560px;
  overflow: hidden;
}

.tracker-flow-shell__bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  border-bottom: 1px solid var(--color-border-subtle);
  background: var(--color-bg-surface);
  padding: 10px 14px;
}

.tracker-flow-shell__eyebrow {
  color: var(--color-fg-muted);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0;
  text-transform: uppercase;
}

.tracker-flow-shell__title {
  margin-top: 2px;
  color: var(--color-fg-default);
  font-size: 14px;
  font-weight: 700;
}

.tracker-flow-shell__stats {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 10px;
  color: var(--color-fg-muted);
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 600;
}

.tracker-flow-shell__right {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 8px 12px;
}

.tracker-task-details-button {
  border-color: color-mix(in srgb, var(--color-accent-primary) 34%, var(--color-border-default));
  background: color-mix(in srgb, var(--color-accent-primary) 8%, var(--color-bg-surface));
  color: var(--color-fg-strong);
  font-weight: var(--fw-semibold);
  box-shadow: 0 1px 1px color-mix(in srgb, var(--color-fg-default) 8%, transparent);
}

.tracker-task-details-button:hover:not(:disabled) {
  border-color: color-mix(in srgb, var(--color-accent-primary) 72%, var(--color-border-strong));
  background: color-mix(in srgb, var(--color-accent-primary) 13%, var(--color-bg-surface));
}

.tracker-task-details-button:active:not(:disabled) {
  background: color-mix(in srgb, var(--color-accent-primary) 17%, var(--color-bg-surface));
}

.tracker-task-details-button:disabled {
  border-color: var(--color-border-default);
  background: var(--color-bg-surface);
  box-shadow: none;
}

.tracker-graph-controls {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 14px;
  border-bottom: 1px solid var(--color-border-subtle);
  background: var(--color-bg-surface);
  padding: 8px 14px;
}

.tracker-graph-filter-group {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.tracker-graph-filter-group__label {
  color: var(--color-fg-muted);
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
}

.tracker-graph-filter {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 24px;
  border: 1px solid var(--color-border-subtle);
  border-radius: 4px;
  background: var(--color-bg-surface-alt);
  color: var(--color-fg-muted);
  padding: 3px 7px;
  font-size: 11px;
  font-weight: 650;
}

.tracker-graph-filter:hover {
  border-color: var(--color-border-strong);
  color: var(--color-fg-default);
}

.tracker-graph-filter strong {
  color: var(--color-fg-default);
  font-family: var(--font-mono);
  font-size: 11px;
}

.tracker-graph-filter--active {
  border-color: var(--color-accent-primary);
  background: color-mix(in srgb, var(--color-accent-primary) 10%, var(--color-bg-surface));
  color: var(--color-fg-default);
}

.tracker-graph-filter--blocked {
  color: var(--color-danger-default);
}

.tracker-graph-filter--open {
  color: var(--color-success-default);
}

.tracker-graph-controls__tail {
  display: flex;
  min-height: 24px;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}

.tracker-graph-clear {
  border: 0;
  background: transparent;
  color: var(--color-fg-muted);
  font-size: 12px;
  font-weight: 700;
}

.tracker-graph-clear:hover {
  color: var(--color-fg-default);
}

.tracker-graph-selection {
  position: absolute;
  top: 12px;
  right: 12px;
  z-index: 8;
  display: flex;
  align-items: center;
  justify-content: space-between;
  max-width: min(520px, calc(100% - 24px));
  gap: 10px;
  border: 1px solid color-mix(in srgb, var(--color-info-default) 22%, var(--color-border-subtle));
  border-radius: 6px;
  background: color-mix(in srgb, var(--color-bg-surface) 96%, var(--color-info-default));
  box-shadow: var(--shadow-sm);
  padding: 7px 9px;
  pointer-events: auto;
}

.tracker-graph-selection__main {
  display: grid;
  min-width: 0;
  gap: 3px;
}

.tracker-graph-selection__eyebrow {
  flex: none;
  border-radius: 999px;
  background: color-mix(in srgb, var(--color-info-default) 9%, var(--color-bg-surface));
  color: var(--color-info-default);
  padding: 2px 6px;
  font-size: 9px;
  font-weight: 800;
  line-height: 1;
  text-transform: uppercase;
}

.tracker-graph-selection__title-row {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 6px;
}

.tracker-graph-selection__title {
  min-width: 0;
  overflow: hidden;
  color: var(--color-fg-default);
  font-size: 12px;
  font-weight: 700;
  line-height: 1.25;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tracker-graph-selection__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  color: var(--color-fg-muted);
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 600;
}

.tracker-graph-selection__actions {
  display: flex;
  flex: none;
  align-items: center;
  gap: 6px;
}

.tracker-flow-frame {
  position: relative;
  flex: 1 1 auto;
  width: 100%;
  min-height: 520px;
  min-width: 0;
}

.tracker-flow {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 520px;
  background: var(--color-bg-surface-alt);
}

.tracker-flow :deep(.vue-flow__edges) {
  z-index: 1;
}

.tracker-flow :deep(.vue-flow__nodes) {
  z-index: 20 !important;
}

:deep(.tracker-node-highlighted .ticket-graph-node) {
  border-color: var(--color-border-strong);
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--color-border-strong) 18%, transparent);
}

:deep(.tracker-node-upstream .ticket-graph-node) {
  border-color: color-mix(in srgb, var(--color-warning-default) 58%, var(--color-border-subtle));
  background: color-mix(in srgb, var(--color-warning-default) 6%, var(--color-bg-surface));
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--color-warning-default) 18%, transparent);
}

:deep(.tracker-node-downstream .ticket-graph-node) {
  border-color: color-mix(in srgb, var(--color-success-default) 52%, var(--color-border-subtle));
  background: color-mix(in srgb, var(--color-success-default) 6%, var(--color-bg-surface));
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--color-success-default) 16%, transparent);
}

:deep(.tracker-node-selected .ticket-graph-node) {
  border-color: var(--color-accent-primary);
  background: color-mix(in srgb, var(--color-accent-primary) 7%, var(--color-bg-surface));
  box-shadow:
    0 0 0 2px color-mix(in srgb, var(--color-accent-primary) 28%, transparent),
    0 6px 14px rgb(15 23 42 / 10%);
}

:deep(.tracker-node-muted) {
  opacity: 0.46;
}

:deep(.tracker-edge-dependency .vue-flow__edge-path) {
  stroke: color-mix(in srgb, var(--color-border-strong) 70%, var(--color-bg-surface));
  stroke-width: 1.4;
}

:deep(.tracker-edge-highlighted .vue-flow__edge-path) {
  stroke: var(--color-border-strong);
  stroke-width: 2.4;
}

:deep(.tracker-edge-upstream .vue-flow__edge-path) {
  stroke: color-mix(in srgb, var(--color-warning-default) 76%, var(--color-fg-muted));
  stroke-width: 3;
}

:deep(.tracker-edge-downstream .vue-flow__edge-path) {
  stroke: color-mix(in srgb, var(--color-success-default) 72%, var(--color-fg-muted));
  stroke-width: 3;
}

:deep(.tracker-edge-muted .vue-flow__edge-path) {
  opacity: 0.2;
}

:deep(.tracker-edge-active .vue-flow__edge-path),
:deep(.tracker-edge-active.selected .vue-flow__edge-path) {
  stroke: var(--color-accent-primary);
  stroke-width: 4;
  filter: drop-shadow(0 0 4px color-mix(in srgb, var(--color-accent-primary) 35%, transparent));
}

@media (max-width: 720px) {
  .tracker-flow-shell__bar {
    display: grid;
  }

  .tracker-flow-shell__stats {
    justify-content: start;
  }

  .tracker-flow-shell__right {
    justify-content: start;
  }

  .tracker-graph-controls {
    flex-wrap: nowrap;
    overflow-x: auto;
    padding-block: 7px;
  }

  .tracker-graph-filter-group {
    flex: none;
    flex-wrap: nowrap;
  }

  .tracker-graph-controls__tail {
    flex: none;
    margin-left: 0;
  }

  .tracker-graph-selection {
    top: 10px;
    right: 10px;
    left: 10px;
    align-items: center;
    max-width: none;
  }

  .tracker-graph-selection__actions {
    justify-content: flex-end;
  }

  .tracker-flow {
    min-height: 520px;
  }
}
</style>
