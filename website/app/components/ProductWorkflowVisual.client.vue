<script setup lang="ts">
import { Background } from '@vue-flow/background'
import { VueFlow, type Edge, type Node } from '@vue-flow/core'

import { productWorkflows, type FlowStatus, type ProductFlowStep } from '~/data/workflows'

const aiTools = [
  { label: 'Codex', logo: '/images/openai.webp' },
  { label: 'Claude Code', logo: '/images/claude.webp' },
  { label: 'Gemini', logo: '/images/gemini.webp' },
]

const hostLogos: Record<string, string> = {
  Codex: '/images/openai.webp',
  'Claude Code': '/images/claude.webp',
  Gemini: '/images/gemini.webp',
}

function hostLogo(host: string) {
  return hostLogos[host] ?? '/images/openai.webp'
}

const activeKey = ref(productWorkflows[0]?.key ?? '')
const isNarrow = ref(false)
const progress = ref(-2)
let mediaQuery: MediaQueryList | undefined
let cycleTimer: ReturnType<typeof setInterval> | undefined

const activeWorkflow = computed(
  () => productWorkflows.find((workflow) => workflow.key === activeKey.value) ?? productWorkflows[0]!,
)

function stepStatus(index: number): FlowStatus {
  if (progress.value < index) return 'waiting'
  if (progress.value === index) return 'active'
  return 'done'
}

function stepStatusLabel(index: number) {
  const status = stepStatus(index)
  if (status === 'done') return 'Done'
  if (status === 'active') return 'Working'
  return 'Waiting'
}

const journeyLabel = computed(() => {
  if (progress.value === -2) return 'Understanding your request'
  if (progress.value === -1) return 'Plan ready for review'
  if (progress.value < activeWorkflow.value.steps.length) {
    return `Step ${progress.value + 1} of ${activeWorkflow.value.steps.length} is moving`
  }
  return 'Workflow complete'
})

const flowSteps = computed<ProductFlowStep[]>(() => [
  {
    id: 'request',
    eyebrow: activeWorkflow.value.host,
    label: 'You describe the outcome',
    detail: activeWorkflow.value.request,
    tone: 'agent',
    code: 'Request received',
    status: progress.value === -2 ? 'active' : 'done',
    statusLabel: progress.value === -2 ? 'Talking' : 'Received',
  },
  {
    id: 'plan',
    eyebrow: 'StackOS',
    label: 'The full plan appears',
    detail: `${activeWorkflow.value.preset} adds the right steps, checks, and handoffs.`,
    tone: 'plan',
    code: 'Review before starting',
    status: progress.value === -2 ? 'waiting' : progress.value === -1 ? 'active' : 'done',
    statusLabel: progress.value === -2 ? 'Building' : progress.value === -1 ? 'Review' : 'Approved',
  },
  ...activeWorkflow.value.steps.map((step, index) => ({
    ...step,
    status: stepStatus(index),
    statusLabel: stepStatusLabel(index),
  })),
])

const nodes = computed<Node[]>(() =>
  flowSteps.value.map((step, index) => ({
    id: step.id,
    type: 'stackos',
    data: step,
    position: isNarrow.value
      ? { x: 24, y: index * 184 + 72 }
      : { x: index * 208 + 18, y: index % 2 === 0 ? 54 : 226 },
    draggable: false,
    selectable: false,
  })),
)

const edges = computed<Edge[]>(() =>
  flowSteps.value.slice(1).map((step, index) => {
    const status = step.status ?? 'waiting'
    return {
      id: `${flowSteps.value[index]?.id}-${step.id}`,
      source: flowSteps.value[index]?.id ?? '',
      target: step.id,
      animated: status === 'active',
      class: `product-flow-edge product-flow-edge--${status}`,
      style: {
        stroke: status === 'done' ? '#74dca5' : status === 'active' ? '#7892ff' : '#3d4656',
        strokeWidth: status === 'active' ? 2.2 : 1.4,
      },
    }
  }),
)

function advanceCycle() {
  progress.value = progress.value >= activeWorkflow.value.steps.length ? -2 : progress.value + 1
}

function replay() {
  progress.value = -2
}

function selectWorkflow(key: string) {
  activeKey.value = key
  replay()
}

function syncViewport(event?: MediaQueryListEvent) {
  isNarrow.value = event?.matches ?? mediaQuery?.matches ?? false
}

onMounted(() => {
  mediaQuery = window.matchMedia('(max-width: 760px)')
  syncViewport()
  mediaQuery.addEventListener('change', syncViewport)
  cycleTimer = setInterval(advanceCycle, 1800)
})

onBeforeUnmount(() => {
  mediaQuery?.removeEventListener('change', syncViewport)
  if (cycleTimer) clearInterval(cycleTimer)
})
</script>

<template>
  <section id="workflow" class="workflow-section section section--ink" aria-labelledby="workflow-title">
    <div class="shell">
      <div v-reveal class="section-heading section-heading--wide">
        <div>
          <p class="eyebrow eyebrow--dark">Keep your AI tool. Add a complete way of working.</p>
          <h2 id="workflow-title">One request becomes<br /><em>a plan you can watch.</em></h2>
        </div>
        <p>
          Ask in Codex, Claude Code, Gemini, or another AI tool. StackOS combines your request
          with a reusable workflow, shows you the full plan, then moves through it step by step.
        </p>
      </div>

      <div v-reveal="70" class="host-strip" aria-label="Compatible AI tools">
        <span>Keep working in</span>
        <template v-for="tool in aiTools" :key="tool.label">
          <strong><img :src="tool.logo" alt="" width="20" height="20" />{{ tool.label }}</strong><i />
        </template>
        <strong>or your preferred AI tool</strong>
      </div>

      <div v-reveal="80" class="workflow-tabs" role="tablist" aria-label="Workflow examples">
        <button
          v-for="workflow in productWorkflows"
          :id="`workflow-tab-${workflow.key}`"
          :key="workflow.key"
          type="button"
          role="tab"
          :aria-selected="activeKey === workflow.key"
          :aria-controls="`workflow-panel-${workflow.key}`"
          :class="{ 'is-active': activeKey === workflow.key }"
          @click="selectWorkflow(workflow.key)"
        >
          <span>{{ workflow.shortLabel }}</span>
          <code>{{ workflow.audience }}</code>
        </button>
      </div>

      <div
        :id="`workflow-panel-${activeWorkflow.key}`"
        :key="activeWorkflow.key"
        v-reveal="120"
        class="workflow-stage"
        role="tabpanel"
        :aria-labelledby="`workflow-tab-${activeWorkflow.key}`"
      >
        <div class="workflow-conversation">
          <div class="workflow-conversation__tool">
            <span><img :src="hostLogo(activeWorkflow.host)" alt="" width="38" height="38" /></span>
            <div><small>Your AI tool</small><strong>{{ activeWorkflow.host }}</strong></div>
          </div>
          <blockquote>“{{ activeWorkflow.request }}”</blockquote>
          <div class="workflow-conversation__plan">
            <small>Saved workflow</small>
            <strong>{{ activeWorkflow.preset }}</strong>
          </div>
        </div>

        <div class="workflow-stage__graph" aria-label="Animated StackOS workflow plan">
          <VueFlow
            :key="`${activeWorkflow.key}-${isNarrow}`"
            :nodes="nodes"
            :edges="edges"
            :fit-view-on-init="true"
            :fit-view-options="{ padding: 0.08 }"
            :min-zoom="0.3"
            :max-zoom="1.1"
            :nodes-draggable="false"
            :nodes-connectable="false"
            :elements-selectable="false"
            :zoom-on-scroll="false"
            :pan-on-scroll="false"
            class="product-flow"
          >
            <Background pattern-color="#293040" :gap="26" :size="1" />
            <template #node-stackos="nodeProps">
              <FlowNode :data="nodeProps.data" :vertical="isNarrow" />
            </template>
          </VueFlow>
          <div class="workflow-stage__status" aria-live="polite">
            <span class="status-dot" /> {{ journeyLabel }}
          </div>
          <button type="button" class="workflow-stage__replay" @click="replay">Replay ↻</button>
        </div>

        <div class="workflow-summary">
          <div class="workflow-summary__copy">
            <p class="eyebrow eyebrow--dark">{{ activeWorkflow.audience }}</p>
            <h3>{{ activeWorkflow.title }}</h3>
            <p>{{ activeWorkflow.description }}</p>
          </div>
          <ol aria-label="Current workflow progress">
            <li
              v-for="(step, index) in activeWorkflow.steps"
              :key="step.id"
              :class="`is-${stepStatus(index)}`"
            >
              <span>{{ stepStatus(index) === 'done' ? '✓' : String(index + 1).padStart(2, '0') }}</span>
              <div><strong>{{ step.label }}</strong><small>{{ stepStatusLabel(index) }}</small></div>
            </li>
          </ol>
          <div class="workflow-outcome">
            <span>When it is done</span>
            <p>{{ activeWorkflow.outcome }}</p>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.workflow-section {
  overflow: clip;
}

.host-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  margin-top: 48px;
  color: var(--ink-soft);
  font-size: 12px;
}

.host-strip > span {
  margin-right: 5px;
  color: var(--ink-muted);
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.host-strip strong {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 8px 11px;
  background: rgb(255 255 255 / 4%);
  border: 1px solid var(--border-strong);
  border-radius: 999px;
}

.host-strip strong img {
  width: 20px;
  height: 20px;
  object-fit: cover;
  border-radius: 6px;
}

.host-strip i {
  width: 3px;
  height: 3px;
  background: var(--signal);
  border-radius: 50%;
}

.workflow-tabs {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  margin: 22px 0;
  padding: 5px;
  background: rgb(255 255 255 / 4%);
  border: 1px solid var(--border-strong);
  border-radius: 16px;
}

.workflow-tabs button {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  min-width: 0;
  padding: 13px 14px;
  color: var(--ink-soft);
  text-align: left;
  background: transparent;
  border: 0;
  border-radius: 11px;
  cursor: pointer;
  transition: color 180ms ease, background 180ms ease;
}

.workflow-tabs button:hover {
  color: var(--paper);
}

.workflow-tabs button.is-active {
  color: var(--paper);
  background: var(--surface-raised);
  box-shadow: inset 0 0 0 1px var(--border-strong);
}

.workflow-tabs span {
  font-size: 13px;
  font-weight: 700;
}

.workflow-tabs code {
  overflow: hidden;
  color: var(--ink-muted);
  font-size: 9px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.workflow-stage {
  overflow: hidden;
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: 24px;
  box-shadow: 0 50px 100px rgb(0 0 0 / 25%);
}

.workflow-conversation {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 18px;
  align-items: center;
  min-height: 86px;
  padding: 16px 20px;
  background: var(--surface-raised);
  border-bottom: 1px solid var(--border-strong);
}

.workflow-conversation__tool {
  display: flex;
  align-items: center;
  gap: 10px;
}

.workflow-conversation__tool > span {
  display: grid;
  width: 38px;
  height: 38px;
  place-items: center;
  overflow: hidden;
  background: var(--surface);
  border-radius: 10px;
}

.workflow-conversation__tool > span img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.workflow-conversation small,
.workflow-conversation strong {
  display: block;
}

.workflow-conversation small {
  margin-bottom: 4px;
  color: var(--ink-muted);
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.workflow-conversation strong {
  font-size: 11px;
}

.workflow-conversation blockquote {
  margin: 0;
  padding: 13px 16px;
  color: var(--paper);
  font-size: 13px;
  font-weight: 650;
  background: #0c0f16;
  border: 1px solid var(--border-strong);
  border-radius: 12px;
}

.workflow-conversation__plan {
  min-width: 150px;
  padding-left: 18px;
  border-left: 1px solid var(--border-strong);
}

.workflow-stage__graph {
  position: relative;
  min-width: 0;
  height: 470px;
  background: radial-gradient(circle at 50% 42%, rgb(91 124 255 / 9%), transparent 45%), #0b0e15;
}

.product-flow {
  height: 100%;
}

.workflow-stage__status,
.workflow-stage__replay {
  position: absolute;
  z-index: 4;
  top: 16px;
  padding: 8px 11px;
  color: var(--ink-soft);
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  background: rgb(10 12 18 / 82%);
  border: 1px solid var(--border-strong);
  border-radius: 99px;
  backdrop-filter: blur(10px);
}

.workflow-stage__status {
  left: 18px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.workflow-stage__replay {
  right: 18px;
  cursor: pointer;
}

.workflow-summary {
  display: grid;
  grid-template-columns: 1.05fr 1.25fr 0.9fr;
  gap: 28px;
  padding: 26px;
  background: var(--surface-raised);
  border-top: 1px solid var(--border-strong);
}

.workflow-summary h3 {
  margin: 9px 0 9px;
  font-size: 21px;
  letter-spacing: -0.04em;
}

.workflow-summary__copy > p:last-child,
.workflow-outcome p {
  margin: 0;
  color: var(--ink-soft);
  font-size: 12px;
  line-height: 1.65;
}

.workflow-summary ol {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.workflow-summary li {
  display: grid;
  grid-template-columns: 24px 1fr;
  gap: 8px;
  align-items: center;
  padding: 8px;
  background: rgb(255 255 255 / 2%);
  border: 1px solid rgb(255 255 255 / 5%);
  border-radius: 8px;
  transition: opacity 260ms ease, border-color 260ms ease, background 260ms ease;
}

.workflow-summary li > span {
  color: var(--ink-muted);
  font-family: var(--font-mono);
  font-size: 9px;
}

.workflow-summary li strong,
.workflow-summary li small {
  display: block;
}

.workflow-summary li strong {
  font-size: 11px;
}

.workflow-summary li small {
  margin-top: 3px;
  color: var(--ink-muted);
  font-family: var(--font-mono);
  font-size: 9px;
}

.workflow-summary li.is-waiting {
  border-color: rgb(255 255 255 / 7%);
}

.workflow-summary li.is-waiting > span,
.workflow-summary li.is-waiting small {
  color: #9aa3b2;
}

.workflow-summary li.is-active {
  background: rgb(91 124 255 / 8%);
  border-color: rgb(91 124 255 / 28%);
}

.workflow-summary li.is-active small {
  color: var(--cyan);
}

.workflow-summary li.is-done > span,
.workflow-summary li.is-done small {
  color: #8ce7b3;
}

.workflow-outcome {
  align-self: stretch;
  padding: 16px;
  background: color-mix(in srgb, var(--signal) 7%, transparent);
  border: 1px solid color-mix(in srgb, var(--signal) 18%, transparent);
  border-radius: 12px;
}

.workflow-outcome span {
  display: block;
  margin-bottom: 9px;
  color: var(--signal);
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

@media (max-width: 980px) {
  .workflow-summary {
    grid-template-columns: 1fr 1fr;
  }

  .workflow-outcome {
    grid-column: 1 / -1;
  }
}

@media (max-width: 760px) {
  .workflow-tabs {
    display: flex;
    overflow-x: auto;
    scroll-snap-type: x mandatory;
  }

  .workflow-tabs button {
    flex: 0 0 145px;
    scroll-snap-align: start;
  }

  .workflow-conversation {
    grid-template-columns: 1fr;
    gap: 12px;
    padding: 18px;
  }

  .workflow-conversation blockquote {
    font-size: 14px;
    line-height: 1.5;
  }

  .workflow-conversation__plan {
    padding: 0;
    border: 0;
  }

  .workflow-stage__graph {
    height: 1420px;
  }

  .workflow-stage__status,
  .workflow-stage__replay {
    top: 14px;
  }

  .workflow-stage__status {
    left: 14px;
  }

  .workflow-stage__replay {
    right: 14px;
  }

  .workflow-summary {
    grid-template-columns: 1fr;
    padding: 21px;
  }

  .workflow-outcome {
    grid-column: auto;
  }
}

:deep(.vue-flow__edge.animated path) {
  animation-duration: 1.2s;
}

@media (prefers-reduced-motion: reduce) {
  :deep(.vue-flow__edge.animated path) {
    animation: none;
  }
}
</style>
