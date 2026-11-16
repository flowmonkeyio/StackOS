import { onBeforeUnmount, onMounted, onUpdated, ref, type Ref } from 'vue'
import { cancelUsagePaint, queueUsagePaint, subscribeUsageVisibility } from '@/lib/usageUpdates'
import { motion } from '@/design/tokens'

// One visibility/resize/theme/DPR observer set for all existing usage canvases.
// Event-driven paint joins the feed's visibility owner; there is no chart clock.
const visibleEntries = new Map<Element, { intersecting: boolean; update: (visible: boolean) => void }>()
const canvases = new Map<Element, () => void>()
const sizeTargets = new Map<Element, Set<() => void>>()
const animations = new Map<Element, Animation>()
let intersection: IntersectionObserver | null = null
let resize: ResizeObserver | null = null
let theme: MutationObserver | null = null
let resolution: MediaQueryList | undefined
let reducedMotion: MediaQueryList | undefined
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
function cancelAnimation(element: Element | null) {
  if (!element) return
  animations.get(element)?.cancel()
  animations.delete(element)
}
function motionChanged() {
  if (reducedMotion?.matches) animations.forEach((_, element) => cancelAnimation(element))
}

export function useUsageVisibility(root: Ref<HTMLElement | null>, changed?: (visible: boolean) => void) {
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
    visibleEntries.set(root.value, { intersecting: typeof IntersectionObserver === 'undefined', update: next => {
      if (visible.value !== next) { visible.value = next; changed?.(next) }
    } })
    intersection?.observe(root.value)
  })
  onBeforeUnmount(() => {
    if (root.value) { intersection?.unobserve(root.value); visibleEntries.delete(root.value) }
    if (!visibleEntries.size) { intersection?.disconnect(); intersection = null; releaseVisibility?.(); releaseVisibility = null }
  })
  return visible
}

// The producer identifies comparable scope and rendered geometry. A finite
// compositor fade acknowledges a changed drawing; canvas pixels are painted once.
export interface UsageCanvasFrame { scope: string; geometry: string }
export function useUsageCanvas(canvas: Ref<HTMLCanvasElement | null>, paint: () => UsageCanvasFrame | void,
  sizeRoot: Ref<HTMLElement | null> = canvas, motionEnabled: () => boolean = () => false) {
  let previous: UsageCanvasFrame | null = null
  const visible = useUsageVisibility(canvas, () => { previous = null; cancelAnimation(canvas.value) })
  const draw = () => {
    if (!visible.value) return
    const frame = paint()
    if (!frame) return
    const changed = previous && previous.scope === frame.scope && previous.geometry !== frame.geometry
    previous = frame
    if (!changed || !motionEnabled() || reducedMotion?.matches || !canvas.value?.animate) return
    cancelAnimation(canvas.value)
    const element = canvas.value, style = getComputedStyle(element)
    const duration = style.getPropertyValue('--duration-base').trim() || motion.duration.base
    const animation = element.animate([{ opacity: 0.65 }, { opacity: 1 }], {
      duration: parseFloat(duration) * (duration.endsWith('ms') ? 1 : 1000),
      easing: style.getPropertyValue('--easing-standard').trim() || motion.easing.standard,
    })
    animations.set(element, animation)
    animation.onfinish = () => { if (animations.get(element) === animation) animations.delete(element) }
  }
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
      reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)')
      reducedMotion?.addEventListener('change', motionChanged)
    }
    canvases.set(canvas.value, schedule)
    if (sizeRoot.value) {
      const schedules = sizeTargets.get(sizeRoot.value) ?? new Set()
      schedules.add(schedule); sizeTargets.set(sizeRoot.value, schedules)
      resize?.observe(sizeRoot.value)
    }
    schedule()
  })
  // Retain the last drawing for comparison; disconnected or invalidated feeds
  // suppress motion until a fresh, enabled paint can animate its replacement.
  onUpdated(() => { if (!motionEnabled()) cancelAnimation(canvas.value); schedule() })
  onBeforeUnmount(() => {
    cancelAnimation(canvas.value)
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
      reducedMotion?.removeEventListener('change', motionChanged); reducedMotion = undefined
    }
  })
  return visible
}
