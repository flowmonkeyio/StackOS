<script setup lang="ts">
import { Handle, Position } from '@vue-flow/core'

import type { ProductFlowStep } from '~/data/workflows'

defineProps<{
  data: ProductFlowStep
  vertical?: boolean
}>()
</script>

<template>
  <div
    class="flow-node"
    :class="[`flow-node--${data.tone}`, `flow-node--${data.status ?? 'waiting'}`]"
  >
    <Handle type="target" :position="vertical ? Position.Top : Position.Left" class="flow-node__handle" />
    <div class="flow-node__topline">
      <span>{{ data.eyebrow }}</span>
      <span class="flow-node__state">
        <i class="flow-node__pulse" aria-hidden="true" />
        {{ data.statusLabel ?? 'Waiting' }}
      </span>
    </div>
    <strong>{{ data.label }}</strong>
    <p>{{ data.detail }}</p>
    <code>{{ data.code }}</code>
    <Handle type="source" :position="vertical ? Position.Bottom : Position.Right" class="flow-node__handle" />
  </div>
</template>

<style scoped>
.flow-node {
  width: 186px;
  min-height: 132px;
  padding: 14px;
  color: var(--paper);
  background: color-mix(in srgb, var(--surface-strong) 94%, transparent);
  border: 1px solid var(--border-strong);
  border-radius: 16px;
  box-shadow: 0 18px 45px rgb(0 0 0 / 24%);
  transition: opacity 320ms ease, border-color 320ms ease, box-shadow 320ms ease, transform 320ms ease;
}

.flow-node--agent {
  border-top-color: var(--cyan);
}

.flow-node--plan {
  border-top-color: var(--cobalt-soft);
}

.flow-node--guard {
  border-top-color: var(--signal);
}

.flow-node--action {
  border-top-color: #ff9b73;
}

.flow-node--evidence {
  border-top-color: #7ee2ad;
}

.flow-node__topline {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  color: var(--ink-muted);
  font-family: var(--font-mono);
  font-size: 9px;
  font-weight: 650;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.flow-node__state {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--ink-muted);
  font-size: 9px;
  letter-spacing: 0.04em;
}

.flow-node__pulse {
  display: inline-block;
  width: 7px;
  height: 7px;
  background: currentColor;
  border-radius: 50%;
  box-shadow: 0 0 0 4px color-mix(in srgb, currentColor 15%, transparent);
}

.flow-node strong {
  display: block;
  margin-bottom: 7px;
  font-size: 14px;
  letter-spacing: -0.02em;
}

.flow-node p {
  min-height: 33px;
  margin: 0 0 12px;
  color: var(--ink-soft);
  font-size: 12px;
  line-height: 1.55;
}

.flow-node code {
  display: inline-flex;
  padding: 4px 7px;
  color: var(--paper);
  background: rgb(255 255 255 / 7%);
  border: 1px solid rgb(255 255 255 / 8%);
  border-radius: 6px;
  font-size: 10px;
}

.flow-node__handle {
  width: 8px;
  height: 8px;
  background: var(--cobalt-soft);
  border: 2px solid var(--surface-strong);
}

.flow-node--waiting {
  border-color: #343c4b;
}

.flow-node--waiting .flow-node__pulse {
  color: #9aa3b2;
  background: currentColor;
  box-shadow: none;
}

.flow-node--active {
  border-color: var(--cobalt-soft);
  box-shadow: 0 20px 60px rgb(91 124 255 / 24%), 0 0 0 1px rgb(91 124 255 / 24%);
  transform: translateY(-3px);
}

.flow-node--active .flow-node__state,
.flow-node--active .flow-node__pulse {
  color: var(--cyan);
}

.flow-node--done {
  border-color: rgb(126 226 173 / 35%);
}

.flow-node--done .flow-node__state,
.flow-node--done .flow-node__pulse {
  color: #8ce7b3;
}

@media (max-width: 760px) {
  .flow-node {
    width: 270px;
    min-height: 146px;
    padding: 16px;
  }

  .flow-node strong {
    font-size: 15px;
  }
}

@media (prefers-reduced-motion: no-preference) {
  .flow-node--active .flow-node__pulse {
    animation: node-pulse 2.4s ease-in-out infinite;
  }

  @keyframes node-pulse {
    50% {
      opacity: 0.45;
      transform: scale(0.8);
    }
  }
}
</style>
