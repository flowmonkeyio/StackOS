<script setup lang="ts">
import { Handle, Position, VueFlow, type Edge, type Node } from '@vue-flow/core'

interface Stage {
  id: string
  title: string
  summary: string
}

const props = defineProps<{ stages: Stage[]; color?: string }>()
const activeIndex = ref(0)
let timer: ReturnType<typeof setInterval> | undefined

const visibleStages = computed(() => props.stages.slice(0, 10))

// Desktop workflow layout contract:
// - four columns maximum and a serpentine reading order;
// - every node owns the same 264 × 210 box;
// - 54px minimum logical gutter between rows for the connector turn;
// - row direction determines both source and target handle sides;
// - long copy is clamped in the diagram and remains complete in the mobile list/accessible label.
const WORKFLOW_COLUMNS = 4
const NODE_COLUMN_PITCH = 316
const NODE_ROW_PITCH = 264
const layoutColumns = computed(() => visibleStages.value.length > 6 ? WORKFLOW_COLUMNS : 3)
const layoutRows = computed(() => Math.ceil(visibleStages.value.length / layoutColumns.value))
const mapStyle = computed(() => ({
  '--workflow-color': props.color || '#7892ff',
  '--workflow-map-height': layoutRows.value > 2 ? '790px' : '680px',
}))

const nodes = computed<Node[]>(() =>
  visibleStages.value.map((stage, index) => {
    const columns = layoutColumns.value
    const row = Math.floor(index / columns)
    const rawColumn = index % columns
    const column = row % 2 === 0 ? rawColumn : columns - 1 - rawColumn
    return {
      id: stage.id,
      type: 'stage',
      position: { x: column * NODE_COLUMN_PITCH, y: row * NODE_ROW_PITCH },
      sourcePosition: row % 2 === 0 ? Position.Right : Position.Left,
      targetPosition: row % 2 === 0 ? Position.Left : Position.Right,
      data: {
        ...stage,
        number: String(index + 1).padStart(2, '0'),
        status: index < activeIndex.value ? 'done' : index === activeIndex.value ? 'working' : 'waiting',
      },
    }
  }),
)

const edges = computed<Edge[]>(() =>
  visibleStages.value.slice(0, -1).map((stage, index) => {
    const nextStage = visibleStages.value[index + 1]!
    return {
      id: `${stage.id}-${nextStage.id}`,
      source: stage.id,
      target: nextStage.id,
      type: 'smoothstep',
      class: {
        'is-complete': index < activeIndex.value,
        'is-current': index === activeIndex.value - 1 || index === activeIndex.value,
      },
      style: {
        stroke: index < activeIndex.value ? props.color || '#7892ff' : '#303746',
        strokeWidth: index === activeIndex.value ? 2.4 : 1.4,
      },
    }
  }),
)

onMounted(() => {
  timer = setInterval(() => {
    activeIndex.value = (activeIndex.value + 1) % visibleStages.value.length
  }, 2200)
})

onBeforeUnmount(() => clearInterval(timer))
</script>

<template>
  <div class="workflow-map" :style="mapStyle">
    <div class="workflow-map__status">
      <span class="status-dot" />
      Step {{ activeIndex + 1 }} of {{ visibleStages.length }} is moving
    </div>

    <div class="workflow-map__canvas">
      <VueFlow
        :nodes="nodes"
        :edges="edges"
        :nodes-draggable="false"
        :nodes-connectable="false"
        :elements-selectable="false"
        :zoom-on-scroll="false"
        :zoom-on-double-click="false"
        :pan-on-drag="true"
        :min-zoom="0.45"
        :max-zoom="1.2"
        fit-view-on-init
        :fit-view-on-init-options="{ padding: 0.12 }"
      >
        <template #node-stage="{ data, targetPosition, sourcePosition }">
          <article
            class="workflow-node"
            :class="`is-${data.status}`"
            :title="`${data.title}: ${data.summary}`"
            :aria-label="`${data.number}. ${data.title}. ${data.summary}. Status: ${data.status}.`"
          >
            <Handle type="target" :position="targetPosition" />
            <div class="workflow-node__top">
              <span>{{ data.number }}</span>
              <b><i />{{ data.status }}</b>
            </div>
            <h3>{{ data.title }}</h3>
            <p>{{ data.summary }}</p>
            <Handle type="source" :position="sourcePosition" />
          </article>
        </template>
      </VueFlow>
    </div>

    <ol class="workflow-map__mobile" aria-label="Workflow stages">
      <li v-for="(stage, index) in visibleStages" :key="stage.id" :class="{ 'is-active': index === activeIndex, 'is-done': index < activeIndex }">
        <span>{{ String(index + 1).padStart(2, '0') }}</span>
        <div><strong>{{ stage.title }}</strong><p>{{ stage.summary }}</p></div>
        <b>{{ index < activeIndex ? 'Done' : index === activeIndex ? 'Working' : 'Waiting' }}</b>
      </li>
    </ol>
  </div>
</template>

<style scoped>
.workflow-map {
  position: relative;
  min-height: var(--workflow-map-height, 650px);
  overflow: hidden;
  color: var(--paper);
  background:
    radial-gradient(circle at 75% 15%, color-mix(in srgb, var(--workflow-color) 14%, transparent), transparent 32%),
    #0b0e14;
  border: 1px solid rgb(255 255 255 / 10%);
  border-radius: 24px;
}

.workflow-map::before {
  position: absolute;
  inset: 0;
  pointer-events: none;
  content: '';
  opacity: 0.35;
  background-image: radial-gradient(rgb(255 255 255 / 12%) 0.7px, transparent 0.7px);
  background-size: 22px 22px;
}

.workflow-map__status {
  position: absolute;
  top: 18px;
  left: 20px;
  z-index: 5;
  display: inline-flex;
  align-items: center;
  gap: 9px;
  padding: 8px 11px;
  color: var(--ink-soft);
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  background: #11151e;
  border: 1px solid rgb(255 255 255 / 10%);
  border-radius: 999px;
}

.workflow-map__canvas {
  position: absolute;
  inset: 62px 12px 12px;
}

.workflow-map__canvas :deep(.vue-flow__edge-path) {
  stroke-linecap: round;
  stroke-linejoin: round;
  transition: stroke 260ms ease, stroke-width 260ms ease, opacity 260ms ease;
}

.workflow-map__canvas :deep(.vue-flow__edge.is-current .vue-flow__edge-path) {
  filter: drop-shadow(0 0 4px color-mix(in srgb, var(--workflow-color) 54%, transparent));
  animation: workflow-edge-pulse 1.45s ease-in-out infinite;
}

.workflow-node {
  width: 264px;
  height: 210px;
  padding: 16px;
  overflow: hidden;
  color: var(--paper);
  background: #141923;
  border: 1px solid #303746;
  border-radius: 15px;
  box-shadow: 0 12px 40px rgb(0 0 0 / 28%);
  transition: border-color 260ms ease, box-shadow 260ms ease;
}

.workflow-node.is-working {
  border-color: var(--workflow-color);
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--workflow-color) 25%, transparent), 0 18px 55px rgb(0 0 0 / 38%);
}

.workflow-node.is-done { border-color: #41735a; }

.workflow-node__top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: var(--ink-muted);
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.07em;
  text-transform: uppercase;
}

.workflow-node__top b {
  display: flex;
  align-items: center;
  gap: 5px;
  font-weight: 600;
}

.workflow-node__top i {
  width: 6px;
  height: 6px;
  background: currentColor;
  border-radius: 50%;
}

.is-working .workflow-node__top b { color: #79dfff; }
.is-done .workflow-node__top b { color: #82dca9; }

.workflow-node h3 {
  display: -webkit-box;
  min-height: 50px;
  margin: 14px 0 7px;
  overflow: hidden;
  color: #f3f1ea;
  font-size: 21px;
  line-height: 1.2;
  letter-spacing: -0.03em;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.workflow-node p {
  display: -webkit-box;
  margin: 0;
  overflow: hidden;
  color: var(--ink-soft);
  font-size: 15.5px;
  line-height: 1.45;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}

.workflow-node :deep(.vue-flow__handle) {
  width: 7px;
  height: 7px;
  background: var(--workflow-color);
  border: 1px solid #0b0e14;
}

.workflow-map__mobile { display: none; }

@keyframes workflow-edge-pulse {
  0%, 100% { opacity: .62; }
  50% { opacity: 1; }
}

@media (max-width: 720px) {
  .workflow-map {
    min-height: 0;
    padding: 70px 14px 15px;
    overflow: visible;
  }

  .workflow-map__canvas { display: none; }

  .workflow-map__mobile {
    position: relative;
    display: grid;
    gap: 10px;
    margin: 0;
    padding: 0;
    list-style: none;
  }

  .workflow-map__mobile li {
    display: grid;
    grid-template-columns: 34px minmax(0, 1fr);
    gap: 2px 11px;
    padding: 15px;
    background: #141923;
    border: 1px solid #303746;
    border-radius: 13px;
  }

  .workflow-map__mobile li.is-active { border-color: var(--workflow-color); }
  .workflow-map__mobile li.is-done { border-color: #41735a; }

  .workflow-map__mobile > li > span {
    grid-row: 1 / 3;
    color: var(--ink-muted);
    font-family: var(--font-mono);
    font-size: 11px;
  }

  .workflow-map__mobile strong {
    font-size: 19px;
  }

  .workflow-map__mobile p {
    margin: 5px 0 0;
    color: var(--ink-soft);
    font-size: 16px;
    line-height: 1.6;
  }

  .workflow-map__mobile b {
    grid-column: 2;
    margin-top: 8px;
    color: var(--ink-muted);
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0.07em;
    text-transform: uppercase;
  }

  .workflow-map__mobile .is-active > b { color: #79dfff; }
  .workflow-map__mobile .is-done > b { color: #82dca9; }
}

@media (prefers-reduced-motion: reduce) {
  .workflow-map__canvas :deep(.vue-flow__edge.is-current .vue-flow__edge-path) { animation: none; }
}
</style>
