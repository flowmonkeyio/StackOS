<script setup lang="ts">
import { computed, ref } from 'vue'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import {
  Handle,
  Position,
  VueFlow,
  type Edge,
  type Node,
  type NodeMouseEvent,
} from '@vue-flow/core'

import type { SchemaWorkflowStepTemplateSpec, SchemaWorkflowTemplateSpec } from '@/api'
import { UiBadge } from '@/components/ui'

import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'

const props = defineProps<{
  spec: SchemaWorkflowTemplateSpec
}>()

type JourneyNodeData = {
  step: SchemaWorkflowStepTemplateSpec
  index: number
  selected: boolean
  approval: boolean
}

const selectedStepId = ref(props.spec.steps[0]?.id ?? '')
const supportsFlow = typeof ResizeObserver !== 'undefined'
const selectedStep = computed(
  () =>
    props.spec.steps.find((step) => step.id === selectedStepId.value) ??
    props.spec.steps[0] ??
    null,
)

const COLUMN_COUNT = 3
const COLUMN_PITCH = 274
const ROW_PITCH = 170

const nodes = computed<Node<JourneyNodeData>[]>(() =>
  props.spec.steps.map((step, index) => {
    const row = Math.floor(index / COLUMN_COUNT)
    const offset = index % COLUMN_COUNT
    const column = row % 2 === 0 ? offset : COLUMN_COUNT - 1 - offset
    const leftToRight = row % 2 === 0
    return {
      id: step.id,
      type: 'journey-step',
      position: { x: column * COLUMN_PITCH, y: row * ROW_PITCH },
      sourcePosition: leftToRight ? Position.Right : Position.Left,
      targetPosition: leftToRight ? Position.Left : Position.Right,
      selectable: false,
      data: {
        step,
        index,
        selected: selectedStep.value?.id === step.id,
        approval: Boolean(step.approval_refs?.length),
      },
    }
  }),
)

const edges = computed<Edge[]>(() => {
  const stepIds = new Set(props.spec.steps.map((step) => step.id))
  return props.spec.steps.flatMap((step, index) => {
    const dependencies = (step.depends_on ?? []).filter((id) => stepIds.has(id))
    const sources = dependencies.length
      ? dependencies
      : index > 0
        ? [props.spec.steps[index - 1]!.id]
        : []
    return sources.map((source) => ({
      id: `${source}-${step.id}`,
      source,
      target: step.id,
      type: 'smoothstep',
      animated: false,
      class:
        selectedStep.value?.id === step.id || selectedStep.value?.id === source
          ? 'journey-edge--active'
          : '',
    }))
  })
})

const selectedIndex = computed(() =>
  selectedStep.value
    ? props.spec.steps.findIndex((step) => step.id === selectedStep.value?.id)
    : -1,
)
const nextSteps = computed(() => {
  if (!selectedStep.value) return []
  const explicit = props.spec.steps.filter((step) =>
    step.depends_on?.includes(selectedStep.value!.id),
  )
  if (explicit.length) return explicit
  const next = props.spec.steps[selectedIndex.value + 1]
  return next ? [next] : []
})
const roles = computed(() => {
  if (!selectedStep.value) return []
  const requirements = props.spec.agent_requirements ?? []
  const explicit = requirements.filter((agent) =>
    agent.applies_to_steps?.includes(selectedStep.value!.id),
  )
  const matches = explicit.length ? explicit : requirements.length === 1 ? requirements : []
  return matches.map((agent) => humanize(agent.role))
})
const canvasStyle = computed(() => ({
  height: `${Math.max(430, Math.ceil(props.spec.steps.length / COLUMN_COUNT) * ROW_PITCH + 80)}px`,
}))
const needs = computed(() =>
  selectedStep.value
    ? [
        ...(selectedStep.value.input_refs ?? []),
        ...(selectedStep.value.context_refs ?? []),
        ...(selectedStep.value.resource_refs ?? []),
      ].map(displayRef)
    : [],
)
const produces = computed(() => (selectedStep.value?.output_refs ?? []).map(displayRef))
const checkpoints = computed(() => (selectedStep.value?.approval_refs ?? []).map(displayRef))

function selectNode(event: NodeMouseEvent): void {
  selectedStepId.value = event.node.id
}

function displayRef(ref: string): string {
  const input = (props.spec.inputs ?? []).find((item) => item.key === ref)
  const output = (props.spec.outputs ?? []).find((item) => item.key === ref)
  const context = (props.spec.context_requirements ?? []).find((item) => item.id === ref)
  const approval = (props.spec.approval_gates ?? []).find((item) => item.key === ref)
  return input?.name || output?.name || context?.purpose || approval?.description || humanize(ref)
}

function humanize(value: string): string {
  return value.replace(/[._:-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}
</script>

<template>
  <section
    class="workflow-journey overflow-hidden rounded-lg border border-default bg-bg-surface"
    aria-labelledby="workflow-journey-title"
  >
    <header class="border-b border-subtle bg-bg-surface-alt px-5 py-4">
      <p class="t-overline text-accent-primary">How the work moves</p>
      <div class="mt-1 flex flex-wrap items-start justify-between gap-3">
        <div class="max-w-3xl">
          <h3 id="workflow-journey-title" class="t-h2 text-fg-strong">Workflow journey</h3>
          <p class="mt-1 text-sm leading-6 text-fg-muted">
            A connected agent follows this route. StackOS keeps the plan, permissions, approvals,
            and progress in one place—it does not do the work itself.
          </p>
        </div>
        <UiBadge tone="accent"> {{ spec.steps.length }} stages </UiBadge>
      </div>
    </header>

    <div
      v-if="spec.steps.length && supportsFlow"
      class="workflow-journey__canvas"
      :style="canvasStyle"
      aria-label="Interactive workflow stages"
    >
      <VueFlow
        :nodes="nodes"
        :edges="edges"
        :nodes-draggable="false"
        :nodes-connectable="false"
        :nodes-focusable="false"
        :edges-focusable="false"
        :elements-selectable="false"
        :zoom-on-scroll="false"
        :zoom-on-pinch="false"
        :pan-on-scroll="false"
        :prevent-scrolling="false"
        :zoom-on-double-click="false"
        :min-zoom="0.42"
        :max-zoom="1.2"
        fit-view-on-init
        :fit-view-on-init-options="{ padding: 0.16 }"
        @node-click="selectNode"
      >
        <template #node-journey-step="{ data, targetPosition, sourcePosition }">
          <button
            type="button"
            class="workflow-journey-node"
            :class="{ 'workflow-journey-node--selected': data.selected }"
            :aria-pressed="data.selected"
            @click="selectedStepId = data.step.id"
          >
            <Handle type="target" :position="targetPosition" />
            <div class="flex items-center justify-between gap-2">
              <span class="workflow-journey-node__number">{{
                String(data.index + 1).padStart(2, '0')
              }}</span>
              <span v-if="data.approval" class="workflow-journey-node__gate">Approval</span>
            </div>
            <h4>{{ data.step.title }}</h4>
            <p>{{ data.step.purpose || 'A defined stage in this workflow.' }}</p>
            <Handle type="source" :position="sourcePosition" />
          </button>
        </template>
        <Background pattern-color="var(--color-border-subtle)" :gap="22" :size="1" />
        <Controls :show-interactive="false" />
      </VueFlow>
    </div>

    <ol v-if="spec.steps.length" class="workflow-journey__mobile" aria-label="Workflow stages">
      <li v-for="(step, index) in spec.steps" :key="step.id">
        <button
          type="button"
          :aria-pressed="selectedStep?.id === step.id"
          @click="selectedStepId = step.id"
        >
          <span>{{ String(index + 1).padStart(2, '0') }}</span>
          <span
            ><strong>{{ step.title }}</strong
            ><small>{{ step.purpose }}</small></span
          >
        </button>
      </li>
    </ol>

    <article v-if="selectedStep" class="workflow-journey__detail">
      <div class="workflow-journey__detail-heading">
        <span>{{ String(selectedIndex + 1).padStart(2, '0') }}</span>
        <div>
          <p class="t-overline text-fg-subtle">Selected stage</p>
          <h4 class="t-h2 text-fg-strong">
            {{ selectedStep.title }}
          </h4>
        </div>
      </div>

      <div class="workflow-journey__detail-grid">
        <section>
          <h5>What happens</h5>
          <p>
            {{ selectedStep.purpose || 'The connected agent completes this part of the plan.' }}
          </p>
          <ul v-if="selectedStep.instructions?.length">
            <li v-for="instruction in selectedStep.instructions" :key="instruction">
              {{ instruction }}
            </li>
          </ul>
        </section>
        <section>
          <h5>Who handles it</h5>
          <p>{{ roles.length ? roles.join(', ') : 'The connected agent running this workflow' }}</p>
          <p class="mt-2 text-xs text-fg-subtle">
            StackOS provides scope, state, and permitted tools.
          </p>
        </section>
        <section>
          <h5>What it needs</h5>
          <ul v-if="needs.length">
            <li v-for="item in needs" :key="item">
              {{ item }}
            </li>
          </ul>
          <p v-else>No additional project input is declared.</p>
        </section>
        <section>
          <h5>What comes out</h5>
          <ul v-if="produces.length">
            <li v-for="item in produces" :key="item">
              {{ item }}
            </li>
          </ul>
          <p v-else>Progress and evidence for the next stage.</p>
        </section>
      </div>

      <div
        v-if="checkpoints.length || selectedStep.success_criteria?.length || nextSteps.length"
        class="workflow-journey__handoff"
      >
        <div v-if="checkpoints.length">
          <strong>Human checkpoint</strong><span>{{ checkpoints.join(' · ') }}</span>
        </div>
        <div v-if="selectedStep.success_criteria?.length">
          <strong>Done when</strong><span>{{ selectedStep.success_criteria.join(' · ') }}</span>
        </div>
        <div>
          <strong>Then</strong
          ><span>{{
            nextSteps.length
              ? nextSteps.map((step) => step.title).join(', ')
              : 'The workflow outcome is ready.'
          }}</span>
        </div>
      </div>
    </article>
  </section>
</template>

<style scoped>
.workflow-journey__canvas {
  min-height: 430px;
  background: var(--color-bg-surface-alt);
}
.workflow-journey__canvas :deep(.vue-flow__edge-path) {
  stroke: var(--color-border-strong);
  stroke-width: 1.4;
}
.workflow-journey__canvas :deep(.journey-edge--active .vue-flow__edge-path) {
  stroke: var(--color-accent-primary);
  stroke-width: 2;
}
.workflow-journey__canvas :deep(.vue-flow__controls) {
  display: flex;
  gap: 4px;
  box-shadow: none;
}
.workflow-journey__canvas :deep(.vue-flow__controls-button) {
  width: 28px;
  height: 28px;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-sm);
  background: var(--color-bg-surface);
  color: var(--color-fg-muted);
}
.workflow-journey-node {
  width: 238px;
  min-height: 132px;
  cursor: pointer;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-md);
  background: var(--color-bg-surface);
  box-shadow: var(--shadow-xs);
  padding: 14px;
  text-align: left;
  transition:
    border-color var(--duration-fast),
    box-shadow var(--duration-fast),
    transform var(--duration-fast);
}
.workflow-journey-node:hover {
  transform: translateY(-2px);
  border-color: var(--color-border-strong);
  box-shadow: var(--shadow-md);
}
.workflow-journey-node:focus-visible {
  outline: 2px solid var(--color-accent-primary);
  outline-offset: 3px;
}
.workflow-journey-node--selected {
  border-color: var(--color-accent-primary);
  box-shadow:
    0 0 0 2px color-mix(in srgb, var(--color-accent-primary) 20%, transparent),
    var(--shadow-md);
}
.workflow-journey-node__number {
  color: var(--color-accent-primary);
  font-family: var(--font-mono);
  font-size: var(--fs-2xs);
  font-weight: var(--fw-semibold);
}
.workflow-journey-node__gate {
  border-radius: 999px;
  background: var(--color-warning-subtle);
  padding: 3px 7px;
  color: var(--color-warning-fg);
  font-size: 10px;
  font-weight: var(--fw-semibold);
}
.workflow-journey-node h4 {
  margin-top: 10px;
  color: var(--color-fg-strong);
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
}
.workflow-journey-node p {
  display: -webkit-box;
  overflow: hidden;
  margin-top: 5px;
  color: var(--color-fg-muted);
  font-size: var(--fs-xs);
  line-height: 1.45;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}
.workflow-journey-node :deep(.vue-flow__handle) {
  width: 7px;
  height: 7px;
  border: 2px solid var(--color-bg-surface);
  background: var(--color-accent-primary);
}
.workflow-journey__mobile {
  display: none;
}
.workflow-journey__detail {
  border-top: 1px solid var(--color-border-subtle);
  padding: 20px;
}
.workflow-journey__detail-heading {
  display: flex;
  align-items: center;
  gap: 12px;
}
.workflow-journey__detail-heading > span {
  display: grid;
  width: 38px;
  height: 38px;
  place-items: center;
  border-radius: var(--radius-md);
  background: var(--color-accent-subtle);
  color: var(--color-accent-primary);
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  font-weight: var(--fw-semibold);
}
.workflow-journey__detail-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-top: 16px;
}
.workflow-journey__detail-grid section {
  min-width: 0;
  border-radius: var(--radius-md);
  background: var(--color-bg-surface-alt);
  padding: 12px;
}
.workflow-journey__detail-grid h5 {
  color: var(--color-fg-strong);
  font-size: var(--fs-xs);
  font-weight: var(--fw-semibold);
}
.workflow-journey__detail-grid p,
.workflow-journey__detail-grid li {
  margin-top: 5px;
  color: var(--color-fg-muted);
  font-size: var(--fs-xs);
  line-height: 1.5;
}
.workflow-journey__detail-grid ul {
  list-style: disc;
  padding-left: 16px;
}
.workflow-journey__handoff {
  display: grid;
  gap: 8px;
  margin-top: 12px;
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border-subtle);
  padding: 12px;
}
.workflow-journey__handoff div {
  display: grid;
  grid-template-columns: 112px minmax(0, 1fr);
  gap: 8px;
  font-size: var(--fs-xs);
}
.workflow-journey__handoff strong {
  color: var(--color-fg-strong);
}
.workflow-journey__handoff span {
  color: var(--color-fg-muted);
}
@media (max-width: 900px) {
  .workflow-journey__canvas {
    display: none;
  }
  .workflow-journey__mobile {
    display: grid;
    gap: 8px;
    padding: 16px;
  }
  .workflow-journey__mobile button {
    display: grid;
    width: 100%;
    grid-template-columns: 32px minmax(0, 1fr);
    gap: 10px;
    border: 1px solid var(--color-border-subtle);
    border-radius: var(--radius-md);
    background: var(--color-bg-surface);
    padding: 12px;
    text-align: left;
  }
  .workflow-journey__mobile button[aria-pressed='true'] {
    border-color: var(--color-accent-primary);
    background: var(--color-accent-subtle);
  }
  .workflow-journey__mobile button > span:first-child {
    color: var(--color-accent-primary);
    font-family: var(--font-mono);
    font-size: var(--fs-2xs);
  }
  .workflow-journey__mobile strong,
  .workflow-journey__mobile small {
    display: block;
  }
  .workflow-journey__mobile strong {
    color: var(--color-fg-strong);
    font-size: var(--fs-sm);
  }
  .workflow-journey__mobile small {
    margin-top: 3px;
    color: var(--color-fg-muted);
    font-size: var(--fs-xs);
  }
  .workflow-journey__detail-grid {
    grid-template-columns: 1fr;
  }
}
</style>
