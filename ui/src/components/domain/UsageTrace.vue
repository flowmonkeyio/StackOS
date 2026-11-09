<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, onUpdated, ref } from 'vue'
import type { SchemaUsageSampleOut } from '@/api'
import { usageTrace, usageTraceDescription, type LiveMetric } from '@/lib/liveUsage'

const props = defineProps<{ samples: SchemaUsageSampleOut[]; metric: LiveMetric; endAt?: number; barCount?: number }>()
const geometry = computed(() => usageTrace(props.samples, props.metric, props.endAt, props.barCount))
const description = computed(() => usageTraceDescription(geometry.value, props.metric))
const canvas = ref<HTMLCanvasElement | null>(null)
let sizeObserver: ResizeObserver | undefined
let themeObserver: MutationObserver | undefined
let resolution: MediaQueryList | undefined

function draw(): void {
  const element = canvas.value
  if (!element) return
  const width = element.clientWidth, height = element.clientHeight
  if (!width || !height) return
  const context = element.getContext('2d')
  if (!context) return
  const ratio = window.devicePixelRatio || 1
  const pixelWidth = Math.round(width * ratio), pixelHeight = Math.round(height * ratio)
  if (element.width !== pixelWidth) element.width = pixelWidth
  if (element.height !== pixelHeight) element.height = pixelHeight
  context.setTransform(pixelWidth / width, 0, 0, pixelHeight / height, 0, 0)
  context.clearRect(0, 0, width, height)
  const style = getComputedStyle(element)
  const muted = style.getPropertyValue('--color-fg-muted').trim() || style.color
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
  const { state, buckets, maximum, baselineAt, startAt } = geometry.value
  const slotWidth = trackWidth / buckets.length
  const barWidth = Math.min(2, slotWidth * 0.65)
  if (state === 'point' && baselineAt != null && startAt != null) {
    const index = Math.floor((baselineAt - startAt) / 60_000)
    context.fillStyle = muted
    context.fillRect(left + (index + 0.5) * slotWidth - barWidth / 2, baseline - 3, barWidth, 3)
  } else if (state === 'bars' && maximum > 0) {
    buckets.forEach((bucket, index) => {
      if (bucket.value == null || bucket.value === 0) return
      const barHeight = Math.max(1, bucket.value / maximum * (height - 4))
      context.fillRect(left + (index + 0.5) * slotWidth - barWidth / 2, baseline - barHeight, barWidth, barHeight)
    })
  }
}

function observeResolution(): void {
  resolution?.removeEventListener('change', resolutionChanged)
  resolution = window.matchMedia?.(`(resolution: ${window.devicePixelRatio || 1}dppx)`)
  resolution?.addEventListener('change', resolutionChanged)
}
function resolutionChanged(): void { draw(); observeResolution() }

onMounted(() => {
  if (typeof ResizeObserver !== 'undefined' && canvas.value) {
    sizeObserver = new ResizeObserver(draw)
    sizeObserver.observe(canvas.value)
  }
  themeObserver = new MutationObserver(draw)
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'class', 'style'] })
  window.addEventListener('resize', draw)
  observeResolution()
  draw()
})
onUpdated(draw)
onBeforeUnmount(() => {
  sizeObserver?.disconnect()
  themeObserver?.disconnect()
  window.removeEventListener('resize', draw)
  resolution?.removeEventListener('change', resolutionChanged)
})
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
