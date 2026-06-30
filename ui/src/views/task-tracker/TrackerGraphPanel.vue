<script setup lang="ts">
import { computed, nextTick, onMounted, onUpdated, ref } from 'vue'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import { useVueFlow, VueFlow } from '@vue-flow/core'
import type { EdgeMouseEvent, NodeMouseEvent, ViewportTransform } from '@vue-flow/core'

import StatusBadge from '@/components/StatusBadge.vue'
import { UiBadge, UiButton } from '@/components/ui'
import { resolveStatus, type Tone } from '@/design/status'
import type { TrackerFlowModel } from '@/lib/task-tracker/graphModel'
import type { TrackerStatus, TrackerTicket } from '@/lib/task-tracker/types'

import TicketGraphNode from './TicketGraphNode.vue'
import type { GraphBlockFilter } from './viewTypes'

import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import '@vue-flow/minimap/dist/style.css'

const TICKET_NODE_WIDTH = 236
const TICKET_NODE_HEIGHT = 96
const READABLE_FOCUS_ZOOM = 0.72

const props = defineProps<{
  flow: TrackerFlowModel
  flowId: string
  flowRenderKey: string
  graphFitOnInit: boolean
  focusNodeIds: string[]
  primaryFocusNodeId: string | null
  refocusKey: string
  initialViewport: ViewportTransform | null
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

const emit = defineEmits<{
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
  (e: 'viewportChangeEnd', value: ViewportTransform): void
  (e: 'viewportReady', value: string): void
}>()

const DEFAULT_VIEWPORT: ViewportTransform = { x: 32, y: 64, zoom: 0.58 }
const initialViewportValue = computed(() => props.initialViewport ?? DEFAULT_VIEWPORT)
const { fitView, setCenter, setViewport } = useVueFlow({ id: props.flowId })
const appliedRefocusKey = ref<string | null>(null)
const viewportReady = ref(false)
const viewportSettled = computed(
  () => viewportReady.value && (!props.refocusKey || props.refocusKey === appliedRefocusKey.value),
)
let lastViewportIntentSignature = ''
let initialViewportRenderKey = ''

const BLOCK_TONES: Record<GraphBlockFilter, Tone> = {
  blocked: 'danger',
  open: 'success',
}

function statusTone(status: TrackerStatus): Tone {
  return resolveStatus('tracker', status).tone
}

async function applyViewportIntent(): Promise<void> {
  await nextTick()
  await applyInitialViewport()
  const pendingRefocusKey =
    props.refocusKey && props.refocusKey !== appliedRefocusKey.value ? props.refocusKey : ''
  if (!props.flow.nodes.length) {
    markViewportReady(pendingRefocusKey)
    return
  }
  const availableNodeIds = new Set(props.flow.nodes.map((node) => node.id))
  const focusNodeIds = props.focusNodeIds.filter((id) => availableNodeIds.has(id))
  const activeFlowNodeId = props.flow.nodes.find((node) => node.data?.active)?.id ?? null
  const primaryFocusNodeId =
    props.primaryFocusNodeId && availableNodeIds.has(props.primaryFocusNodeId)
      ? props.primaryFocusNodeId
      : (activeFlowNodeId ?? focusNodeIds[0] ?? null)
  const shouldRefocus = Boolean(props.refocusKey) && props.refocusKey !== appliedRefocusKey.value
  if (shouldRefocus && primaryFocusNodeId) {
    const primaryNode = props.flow.nodes.find((node) => node.id === primaryFocusNodeId)
    const refocusKey = props.refocusKey
    appliedRefocusKey.value = refocusKey
    if (primaryNode) {
      const center = nodeCenter(primaryNode)
      await setCenter(center.x, center.y, {
        zoom: READABLE_FOCUS_ZOOM,
        duration: viewportReady.value ? 200 : 0,
      })
      markViewportReady(refocusKey)
      return
    }
  }
  if (props.graphFitOnInit && !props.initialViewport && !appliedRefocusKey.value && !viewportReady.value) {
    await fitView({
      padding: 0.22,
      minZoom: 0.16,
      maxZoom: 0.98,
      duration: viewportReady.value ? 160 : 0,
    })
  }
  markViewportReady(pendingRefocusKey)
}

async function applyInitialViewport(): Promise<void> {
  if (!props.initialViewport || initialViewportRenderKey === props.flowRenderKey) return
  initialViewportRenderKey = props.flowRenderKey
  await setViewport(props.initialViewport, { duration: 0 })
}

function markViewportReady(refocusKey = ''): void {
  if (refocusKey) appliedRefocusKey.value = refocusKey
  viewportReady.value = true
  emit('viewportReady', refocusKey)
}

function nodeCenter(node: TrackerFlowModel['nodes'][number]): { x: number; y: number } {
  return {
    x: node.position.x + nodeWidth(node) / 2,
    y: node.position.y + nodeHeight(node) / 2,
  }
}

function nodeWidth(node: TrackerFlowModel['nodes'][number]): number {
  return numericStyleValue(styleValue(node, 'width')) ?? TICKET_NODE_WIDTH
}

function nodeHeight(node: TrackerFlowModel['nodes'][number]): number {
  return numericStyleValue(styleValue(node, 'height')) ?? TICKET_NODE_HEIGHT
}

function styleValue(node: TrackerFlowModel['nodes'][number], key: 'width' | 'height'): unknown {
  return typeof node.style === 'function' ? null : node.style?.[key]
}

function numericStyleValue(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value !== 'string') return null
  const parsed = Number.parseFloat(value)
  return Number.isFinite(parsed) ? parsed : null
}

async function onNodesInitialized(): Promise<void> {
  viewportReady.value = false
  await applyViewportIntent()
}

onUpdated(() => {
  const signature = [
    props.flowRenderKey,
    props.refocusKey,
    props.flow.nodes.length,
  ].join(':')
  if (signature !== lastViewportIntentSignature) {
    lastViewportIntentSignature = signature
    if (props.refocusKey && props.refocusKey !== appliedRefocusKey.value) {
      viewportReady.value = false
    }
    void applyViewportIntent()
  }
})

onMounted(() => {
  void applyViewportIntent()
})
</script>

<template>
  <section
    class="tracker-flow-shell rounded-lg border border-default bg-bg-surface shadow-xs"
    aria-label="Task dependency graph"
  >
    <header class="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-subtle px-4 py-3">
      <div class="min-w-0">
        <p class="t-overline text-fg-subtle">
          Dependency map
        </p>
        <h3 class="t-h3 truncate text-fg-strong">
          {{ activeTaskTitle }}
        </h3>
      </div>
      <div class="flex flex-wrap items-center justify-end gap-x-3 gap-y-2">
        <p class="tracker-flow-shell__stats text-xs text-fg-muted">
          <span>{{ ticketStatLabel }}</span>
          <span>{{ edgeStatLabel }}</span>
        </p>
        <UiButton
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
    </header>

    <div class="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-subtle bg-bg-surface px-4 py-2">
      <div class="flex flex-wrap items-center gap-1.5">
        <span class="t-overline text-fg-subtle">Status</span>
        <UiBadge
          v-for="item in statusRows"
          :key="item.key"
          interactive
          :tone="statusTone(item.key)"
          :variant="statusFilters.includes(item.key) ? 'solid' : 'subtle'"
          :aria-pressed="statusFilters.includes(item.key)"
          @click="$emit('toggleStatus', item.key)"
        >
          {{ item.label }}
          <template #iconRight>
            <span class="tabular-nums">{{ item.count }}</span>
          </template>
        </UiBadge>
      </div>
      <div class="flex flex-wrap items-center gap-1.5">
        <span class="t-overline text-fg-subtle">Block</span>
        <UiBadge
          v-for="item in blockRows"
          :key="item.key"
          interactive
          :tone="BLOCK_TONES[item.key]"
          :variant="blockFilters.includes(item.key) ? 'solid' : 'subtle'"
          :aria-pressed="blockFilters.includes(item.key)"
          @click="$emit('toggleBlock', item.key)"
        >
          {{ item.label }}
          <template #iconRight>
            <span class="tabular-nums">{{ item.count }}</span>
          </template>
        </UiBadge>
      </div>
      <div class="ml-auto flex items-center">
        <UiButton
          v-if="filtersActive"
          variant="ghost"
          size="sm"
          @click="$emit('clearFilters')"
        >
          Clear graph filters
        </UiButton>
      </div>
    </div>

    <div
      class="tracker-flow-frame"
      :class="{ 'tracker-flow-frame--settled': viewportSettled }"
      @click.capture="$emit('graphCanvasClick', $event)"
    >
      <VueFlow
        :key="flowRenderKey"
        :id="flowId"
        class="tracker-flow"
        :class="{ 'tracker-flow--settled': viewportSettled }"
        :nodes="flow.nodes"
        :edges="flow.edges"
        :default-viewport="initialViewportValue"
        :fit-view-on-init="false"
        :min-zoom="0.12"
        :max-zoom="1.5"
        pan-on-scroll
        @nodes-initialized="onNodesInitialized"
        @viewport-change-end="emit('viewportChangeEnd', $event)"
        @node-click="$emit('nodeClick', $event)"
        @edge-click="$emit('edgeClick', $event)"
        @pane-click="$emit('paneClick', $event)"
      >
        <template #node-tracker-ticket="props">
          <TicketGraphNode v-bind="props" />
        </template>
        <Background pattern-color="var(--color-border-subtle)" />
        <MiniMap
          pannable
          zoomable
        />
        <Controls />
        <div
          v-if="selectionVisible"
          class="tracker-graph-selection"
          @pointerdown.stop
          @mousedown.stop
        >
          <div class="grid min-w-0 gap-1">
            <div class="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
              <p class="t-overline text-fg-subtle">
                {{ selectionLabel }}
              </p>
              <StatusBadge
                v-if="selectedTicket"
                domain="tracker"
                :status="selectedTicket.status"
              />
            </div>
            <p class="truncate text-sm font-medium text-fg-strong">
              {{ selectedTicket?.title ?? 'Dependency context' }}
            </p>
            <p class="tracker-graph-selection__meta text-2xs text-fg-muted">
              <span
                v-if="selectedTicket"
                class="font-mono"
              >{{ selectedTicket.key }}</span>
              <span v-if="selectedEdgeLabel">{{ selectedEdgeLabel }}</span>
              <span
                v-for="stat in selectionStats"
                :key="stat"
              >{{ stat }}</span>
              <span v-if="selectedTicket?.assignee">Owner {{ selectedTicket.assignee }}</span>
              <span v-if="selectedTicket?.run_plan_id">Run {{ selectedTicket.run_plan_id }}</span>
            </p>
          </div>
          <div class="tracker-graph-selection__actions flex flex-none items-center gap-1.5">
            <UiButton
              v-if="selectedTicket"
              variant="secondary"
              size="sm"
              @click.stop="$emit('openSelectedDetail')"
            >
              Details
            </UiButton>
            <UiButton
              variant="ghost"
              size="sm"
              @click.stop="$emit('clearGraphFocus')"
            >
              Clear
            </UiButton>
          </div>
        </div>
      </VueFlow>
    </div>
  </section>
</template>

<style scoped>
.tracker-flow-shell {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-height: 560px;
  overflow: hidden;
}

.tracker-flow-shell__stats {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  column-gap: 8px;
  row-gap: 2px;
}

.tracker-flow-shell__stats > span + span::before {
  content: '·';
  margin-right: 8px;
  color: var(--color-fg-subtle);
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
  gap: 12px;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-lg);
  background: var(--color-bg-surface);
  box-shadow: var(--shadow-md);
  padding: 8px 10px;
  pointer-events: auto;
}

.tracker-graph-selection__meta {
  display: flex;
  flex-wrap: wrap;
  column-gap: 8px;
  row-gap: 2px;
}

.tracker-flow-frame {
  position: relative;
  flex: 1 1 auto;
  width: 100%;
  min-height: 520px;
  min-width: 0;
  opacity: 0;
  pointer-events: none;
}

.tracker-flow-frame--settled {
  opacity: 1;
  pointer-events: auto;
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

/* Zoom controls — small icon buttons on the surface material. */
.tracker-flow :deep(.vue-flow__controls) {
  display: flex;
  flex-direction: column;
  gap: 4px;
  box-shadow: none;
}

.tracker-flow :deep(.vue-flow__controls-button) {
  box-sizing: border-box;
  width: 26px;
  height: 26px;
  padding: 5px;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-sm);
  background: var(--color-bg-surface);
  box-shadow: var(--shadow-xs);
  color: var(--color-fg-muted);
}

.tracker-flow :deep(.vue-flow__controls-button svg) {
  fill: currentColor;
}

.tracker-flow :deep(.vue-flow__controls-button:hover) {
  border-color: var(--color-border-strong);
  background: var(--color-bg-surface-alt);
  color: var(--color-fg-default);
}

/* Minimap — surface material with sunken viewport mask. */
.tracker-flow :deep(.vue-flow__minimap) {
  overflow: hidden;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-md);
  background-color: var(--color-bg-surface);
  box-shadow: var(--shadow-xs);
}

.tracker-flow :deep(.vue-flow__minimap-mask) {
  fill: color-mix(in srgb, var(--color-bg-sunken) 72%, transparent);
}

.tracker-flow :deep(.vue-flow__minimap-node) {
  fill: var(--color-border-strong);
  stroke: none;
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
  background: var(--color-bg-surface);
  box-shadow: var(--shadow-md);
}

:deep(.tracker-node-muted) {
  opacity: 0.46;
}

:deep(.tracker-edge-dependency .vue-flow__edge-path) {
  stroke: color-mix(in srgb, var(--color-border-strong) 70%, var(--color-bg-surface));
  stroke-width: 1.2;
}

:deep(.tracker-edge-highlighted .vue-flow__edge-path) {
  stroke: var(--color-border-strong);
  stroke-width: 1.2;
}

:deep(.tracker-edge-upstream .vue-flow__edge-path) {
  stroke: color-mix(in srgb, var(--color-warning-default) 76%, var(--color-fg-muted));
  stroke-width: 1.2;
}

:deep(.tracker-edge-downstream .vue-flow__edge-path) {
  stroke: color-mix(in srgb, var(--color-success-default) 72%, var(--color-fg-muted));
  stroke-width: 1.2;
}

:deep(.tracker-edge-muted .vue-flow__edge-path) {
  opacity: 0.2;
}

:deep(.tracker-edge-active .vue-flow__edge-path),
:deep(.tracker-edge-active.selected .vue-flow__edge-path) {
  stroke: var(--color-accent-primary);
  stroke-width: 1.2;
}

@media (max-width: 720px) {
  .tracker-graph-selection {
    top: 10px;
    right: 10px;
    left: 10px;
    max-width: none;
  }

  .tracker-graph-selection__actions {
    justify-content: flex-end;
  }
}
</style>
