import { onBeforeUnmount, onMounted, onUpdated, ref, type Ref } from 'vue'
import { cancelUsagePaint, queueUsagePaint, subscribeUsageVisibility } from '@/lib/usageUpdates'

// One visibility/resize/theme/DPR observer set for all existing usage canvases.
// Event-driven paint joins the feed's visibility owner; there is no chart clock.
const visibleEntries = new Map<Element, { intersecting: boolean; update: (visible: boolean) => void }>()
const canvases = new Map<Element, () => void>()
const sizeTargets = new Map<Element, Set<() => void>>()
let intersection: IntersectionObserver | null = null
let resize: ResizeObserver | null = null
let theme: MutationObserver | null = null
let resolution: MediaQueryList | undefined
let releaseVisibility: (() => void) | null = null

function visibilityChanged() {
  visibleEntries.forEach(entry => entry.update(entry.intersecting && !document.hidden))
}
function invalidate() { canvases.forEach(schedule => schedule()) }
function observeResolution() {
  resolution?.removeEventListener('change', resolutionChanged)
  resolution = window.matchMedia?.(`(resolution: ${window.devicePixelRatio || 1}dppx)`)
  resolution?.addEventListener('change', resolutionChanged)
}
function resolutionChanged() { invalidate(); observeResolution() }

export function useUsageVisibility(root: Ref<HTMLElement | null>) {
  const visible = ref(typeof IntersectionObserver === 'undefined' && !document.hidden)
  onMounted(() => {
    if (!root.value) return
    if (!visibleEntries.size) {
      releaseVisibility = subscribeUsageVisibility(visibilityChanged)
      if (typeof IntersectionObserver !== 'undefined') intersection = new IntersectionObserver(entries => {
        for (const observation of entries) {
          const entry = visibleEntries.get(observation.target)
          if (entry) { entry.intersecting = observation.isIntersecting; entry.update(observation.isIntersecting && !document.hidden) }
        }
      }, { rootMargin: '160px 0px' })
    }
    visibleEntries.set(root.value, { intersecting: typeof IntersectionObserver === 'undefined', update: next => { visible.value = next } })
    intersection?.observe(root.value)
  })
  onBeforeUnmount(() => {
    if (root.value) { intersection?.unobserve(root.value); visibleEntries.delete(root.value) }
    if (!visibleEntries.size) { intersection?.disconnect(); intersection = null; releaseVisibility?.(); releaseVisibility = null }
  })
  return visible
}

export function useUsageCanvas(canvas: Ref<HTMLCanvasElement | null>, paint: () => void, sizeRoot: Ref<HTMLElement | null> = canvas) {
  const visible = useUsageVisibility(canvas)
  const draw = () => { if (visible.value) paint() }
  const schedule = () => { if (visible.value) queueUsagePaint(draw) }
  onMounted(() => {
    if (!canvas.value) return
    if (!canvases.size) {
      if (typeof ResizeObserver !== 'undefined') resize = new ResizeObserver(entries => {
        for (const entry of entries) sizeTargets.get(entry.target)?.forEach(schedule => schedule())
      })
      theme = new MutationObserver(invalidate)
      theme.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'class', 'style'] })
      window.addEventListener('resize', invalidate)
      observeResolution()
    }
    canvases.set(canvas.value, schedule)
    if (sizeRoot.value) {
      const schedules = sizeTargets.get(sizeRoot.value) ?? new Set()
      schedules.add(schedule); sizeTargets.set(sizeRoot.value, schedules)
      resize?.observe(sizeRoot.value)
    }
    schedule()
  })
  onUpdated(schedule)
  onBeforeUnmount(() => {
    cancelUsagePaint(draw)
    if (canvas.value) canvases.delete(canvas.value)
    if (sizeRoot.value) {
      const schedules = sizeTargets.get(sizeRoot.value)
      schedules?.delete(schedule)
      if (!schedules?.size) { resize?.unobserve(sizeRoot.value); sizeTargets.delete(sizeRoot.value) }
    }
    if (!canvases.size) {
      resize?.disconnect(); resize = null; theme?.disconnect(); theme = null
      window.removeEventListener('resize', invalidate)
      resolution?.removeEventListener('change', resolutionChanged); resolution = undefined
    }
  })
  return visible
}
