<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, onUpdated, ref } from 'vue'
import type { SchemaUsageSampleOut } from '@/api'
import { usageTrace, type LiveMetric } from '@/lib/liveUsage'

const props = defineProps<{ samples: SchemaUsageSampleOut[]; metric: LiveMetric }>()
const geometry = computed(() => usageTrace(props.samples, props.metric))
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
  const color = getComputedStyle(element).color
  context.strokeStyle = color
  context.fillStyle = color
  context.lineWidth = 1.3
  context.lineJoin = 'round'
  context.lineCap = 'round'
  context.setLineDash([])
  context.beginPath()
  const { state, points } = geometry.value
  if (state === 'empty') {
    context.lineWidth = 1
    context.setLineDash([2, 3])
    context.moveTo(2, height / 2)
    context.lineTo(width - 2, height / 2)
    context.stroke()
  } else if (state === 'point') {
    context.arc(width / 2, height / 2, 1.7, 0, Math.PI * 2)
    context.fill()
  } else {
    points.forEach((point, i) => {
      const x = point.x / 28 * width, y = point.y / 12 * height
      if (!i) context.moveTo(x, y)
      else {
        context.lineTo(x, points[i - 1]!.y / 12 * height)
        context.lineTo(x, y)
      }
    })
    context.stroke()
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
    aria-hidden="true"
  />
</template>

<style scoped>
.usage-trace { display: block; width: 64px; max-width: 100%; height: 16px; flex: none; color: var(--color-info-fg); }
.usage-trace[data-metric='cache_read'] { color: var(--color-accent-primary); }
.usage-trace[data-metric='output'] { color: var(--color-success-fg); }
.usage-trace[data-state='empty'] { color: var(--color-fg-muted); opacity: 0.5; }
</style>
