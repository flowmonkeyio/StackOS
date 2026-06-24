<!--
  UiSparkline — small trend line for a numeric time series.

  Extracted from CostBudget's hand-rolled inline SVG so trends render the same
  everywhere. Pass points {label, value, display?}; the path/dots/tooltips are
  computed internally over a 200×40 viewBox. Stroke follows `tone`.
-->
<script setup lang="ts">
import { computed } from 'vue'

export interface SparklinePoint {
  label: string
  value: number
  /** Tooltip value text; defaults to the raw value. */
  display?: string
}

const props = withDefaults(
  defineProps<{
    points: SparklinePoint[]
    ariaLabel: string
    tone?: 'accent' | 'success' | 'warning' | 'danger'
    /** Rendered height in px (viewBox is fixed). */
    height?: number
  }>(),
  { tone: 'accent', height: 60 },
)

const VIEW_W = 200
const VIEW_H = 40

const stroke = computed(
  () =>
    ({
      accent: 'var(--color-accent-primary)',
      success: 'var(--color-success-default)',
      warning: 'var(--color-warning-default)',
      danger: 'var(--color-danger-default)',
    })[props.tone],
)

const plotted = computed(() => {
  const n = props.points.length
  if (n === 0) return []
  const max = Math.max(...props.points.map((p) => p.value), 1)
  return props.points.map((p, i) => ({
    ...p,
    x: (i / Math.max(n - 1, 1)) * VIEW_W,
    y: VIEW_H - (p.value / max) * VIEW_H,
  }))
})

const path = computed(() =>
  plotted.value
    .map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`)
    .join(' '),
)
</script>

<template>
  <svg
    :viewBox="`0 0 ${VIEW_W} ${VIEW_H}`"
    width="100%"
    :height="height"
    role="img"
    :aria-label="ariaLabel"
    preserveAspectRatio="none"
  >
    <path
      :d="path"
      fill="none"
      stroke-width="2"
      :style="{ stroke }"
      vector-effect="non-scaling-stroke"
    />
    <circle
      v-for="p in plotted"
      :key="p.label"
      :cx="p.x"
      :cy="p.y"
      r="2"
      :style="{ fill: stroke }"
      vector-effect="non-scaling-stroke"
    >
      <title>{{ p.label }}: {{ p.display ?? p.value }}</title>
    </circle>
  </svg>
</template>
