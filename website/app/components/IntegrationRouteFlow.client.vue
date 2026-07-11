<script setup lang="ts">
import { Handle, Position, VueFlow, useVueFlow, type Edge, type Node } from '@vue-flow/core'

const props = defineProps<{ provider: string; providerSlug: string; color: string }>()
const flowId = `integration-route-${props.providerSlug}`
const compact = ref(false)
const root = ref<HTMLElement | null>(null)
const { fitView } = useVueFlow({ id: flowId })
let observer: ResizeObserver | undefined

const steps = computed(() => [
  { id: 'request', title: 'Your request', note: 'Start in your AI' },
  { id: 'plan', title: 'StackOS plan', note: 'Expands the steps' },
  { id: 'provider', title: props.provider, note: 'Does its part' },
  { id: 'result', title: 'Checked result', note: 'Keeps the proof' },
])

const nodes = computed<Node[]>(() => steps.value.map((step, index) => ({
  id: step.id,
  type: 'route-step',
  position: compact.value ? { x: 0, y: index * 124 } : { x: index * 228, y: 0 },
  sourcePosition: compact.value ? Position.Bottom : Position.Right,
  targetPosition: compact.value ? Position.Top : Position.Left,
  data: { ...step, number: String(index + 1).padStart(2, '0') },
})))

const edges = computed<Edge[]>(() => steps.value.slice(0, -1).map((step, index) => ({
  id: `${step.id}-${steps.value[index + 1]!.id}`,
  source: step.id,
  target: steps.value[index + 1]!.id,
  type: 'straight',
  animated: true,
  style: { stroke: props.color, strokeWidth: 1.6 },
})))

async function updateLayout(width: number) {
  const nextCompact = width < 620
  if (nextCompact !== compact.value) compact.value = nextCompact
  await nextTick()
  await fitView({ padding: compact.value ? 0.08 : 0.12, duration: 0 })
}

onMounted(async () => {
  await nextTick()
  if (!root.value) return
  observer = new ResizeObserver(([entry]) => updateLayout(entry!.contentRect.width))
  observer.observe(root.value)
})
onBeforeUnmount(() => observer?.disconnect())
</script>

<template>
  <div ref="root" class="integration-route-flow" :class="{ 'is-compact': compact }" :style="{ '--route-color': color }">
    <VueFlow
      :id="flowId"
      :nodes="nodes"
      :edges="edges"
      :nodes-draggable="false"
      :nodes-connectable="false"
      :elements-selectable="false"
      :pan-on-drag="false"
      :zoom-on-scroll="false"
      :zoom-on-double-click="false"
      :prevent-scrolling="false"
      fit-view-on-init
      :fit-view-on-init-options="{ padding: 0.12 }"
    >
      <template #node-route-step="{ data, targetPosition, sourcePosition }">
        <article class="integration-route-node">
          <Handle type="target" :position="targetPosition" />
          <span>{{ data.number }}</span>
          <strong>{{ data.title }}</strong>
          <small>{{ data.note }}</small>
          <Handle type="source" :position="sourcePosition" />
        </article>
      </template>
    </VueFlow>
  </div>
</template>

<style scoped>
.integration-route-flow { width: 100%; height: 210px; margin-top: 38px; overflow: hidden; background: #f4f2eb; border: 1px solid var(--paper-border); border-radius: 15px; }
.integration-route-flow.is-compact { height: 560px; }
.integration-route-flow :deep(.vue-flow__pane) { cursor: default; }
.integration-route-flow :deep(.vue-flow__edge-path) { stroke-linecap: round; }
.integration-route-flow :deep(.vue-flow__edge.animated path) { animation-duration: 1.2s; }
.integration-route-node { display: grid; width: 186px; min-height: 104px; align-content: center; padding: 15px 17px; color: var(--ink); background: #fff; border: 1px solid #d5d2c7; border-radius: 12px; box-shadow: 0 9px 28px rgb(29 32 38 / 7%); }
.integration-route-node span { color: var(--cobalt); font-family: var(--font-mono); font-size: 10px; }
.integration-route-node strong { margin-top: 9px; overflow: hidden; font-size: 16px; line-height: 1.15; text-overflow: ellipsis; white-space: nowrap; }
.integration-route-node small { margin-top: 5px; color: #666760; font-size: 12px; }
.integration-route-node :deep(.vue-flow__handle) { width: 7px; height: 7px; background: var(--route-color); border: 1px solid #fff; }
</style>
