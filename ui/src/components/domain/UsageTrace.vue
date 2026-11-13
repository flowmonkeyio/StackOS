<script setup lang="ts">
import { computed, ref } from 'vue'
import type { SchemaUsageSampleOut } from '@/api'
import { prepareUsageSamples, preparedUsageTrace, usageTrace, usageTraceDescription, usageWindowAnchor, type LiveMetric, type UsageTraceGeometry } from '@/lib/liveUsage'
import { useUsageCanvas, type UsageCanvasFrame } from '@/composables/useUsageCanvas'

const props = defineProps<{ samples: SchemaUsageSampleOut[]; metric: LiveMetric; endAt?: number; barCount?: number; preparedGeometry?: UsageTraceGeometry; active?: boolean }>()
const canvas = ref<HTMLCanvasElement | null>(null)
const visible = useUsageCanvas(canvas, draw, canvas, () => !!props.active)
const prepared = computed(() => visible.value && !props.preparedGeometry ? prepareUsageSamples(props.samples) : null)
const anchor = computed(() => prepared.value ? usageWindowAnchor(prepared.value, props.endAt) : undefined)
const empty = usageTrace([], 'input')
const geometry = computed(() => visible.value ? props.preparedGeometry ?? (prepared.value ? preparedUsageTrace(prepared.value, props.metric, anchor.value, props.barCount) : empty) : empty)
const description = computed(() => usageTraceDescription(geometry.value, props.metric))
let signature = ''

function draw(): UsageCanvasFrame | void {
  const element = canvas.value
  if (!element) return
  const width = element.clientWidth, height = element.clientHeight
  if (!width || !height) return
  const ratio = window.devicePixelRatio || 1
  const style = getComputedStyle(element)
  const muted = style.getPropertyValue('--color-fg-muted').trim() || style.color
  const next = JSON.stringify([geometry.value, width, height, ratio, style.color, muted])
  if (next === signature) return
  const context = element.getContext('2d')
  if (!context) return
  signature = next
  const pixelWidth = Math.round(width * ratio), pixelHeight = Math.round(height * ratio)
  if (element.width !== pixelWidth) element.width = pixelWidth
  if (element.height !== pixelHeight) element.height = pixelHeight
  context.setTransform(pixelWidth / width, 0, 0, pixelHeight / height, 0, 0)
  context.clearRect(0, 0, width, height)
  const baseline = height - 1.5
  const left = 1, trackWidth = width - 2
  context.strokeStyle = muted
  context.fillStyle = style.color
  context.lineWidth = 1
  context.setLineDash([])
  context.globalAlpha = 0.35
  context.beginPath()
  context.moveTo(left, baseline)
  context.lineTo(width - left, baseline)
  context.stroke()
  context.globalAlpha = 1
  const { buckets, maximum } = geometry.value
  const slotWidth = trackWidth / buckets.length
  const barWidth = Math.min(2, slotWidth * 0.65)
  if (maximum > 0) {
    buckets.forEach((bucket, index) => {
      if (bucket.value == null || bucket.value === 0) return
      const barHeight = Math.max(1, bucket.value / maximum * (height - 4))
      context.fillRect(left + (index + 0.5) * slotWidth - barWidth / 2, baseline - barHeight, barWidth, barHeight)
    })
  }
  context.fillStyle = muted
  buckets.forEach((bucket, index) => {
    if (!bucket.observations.length) return
    context.fillRect(left + (index + 0.5) * slotWidth - barWidth / 2, baseline - 3, barWidth, 3)
  })
  return { scope: JSON.stringify([props.metric, buckets.length, width, height]), geometry: JSON.stringify(buckets.map(bucket => [
    maximum > 0 && bucket.value != null ? Math.max(0, bucket.value / maximum) : 0, !!bucket.observations.length,
  ])) }
}

</script>

<template>
  <canvas
    ref="canvas"
    class="usage-trace"
    :data-metric="metric"
    :data-state="geometry.state"
    role="img"
    :aria-label="description"
    :title="description"
  />
</template>

<style scoped>
.usage-trace { display: block; width: 32px; max-width: 100%; height: 16px; flex: none; color: var(--color-info-fg); }
.usage-trace[data-metric='cache_read'] { color: var(--color-accent-primary); }
.usage-trace[data-metric='output'] { color: var(--color-success-fg); }
</style>
