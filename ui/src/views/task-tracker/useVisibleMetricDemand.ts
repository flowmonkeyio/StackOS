import { onBeforeUnmount, onMounted, onUpdated, ref, type Ref } from 'vue'

// One viewport batch, with 160px (roughly one card) of overscan. Scroll roots
// clip independently; offscreen columns never contribute metric identities.
export const METRIC_OVERSCAN_PX = 160
export function visibleMetricKeys(root: HTMLElement, selector: string, key: (node: HTMLElement) => string | undefined): string[] {
  const bounds = root.getBoundingClientRect()
  if (!bounds.width || !bounds.height) return []
  return [...root.querySelectorAll<HTMLElement>(selector)].flatMap(node => {
    const rect = node.getBoundingClientRect()
    const column = node.closest<HTMLElement>('.tracker-board__cards')?.getBoundingClientRect() ?? bounds
    const top = Math.max(bounds.top, column.top)
    const bottom = Math.min(bounds.bottom, column.bottom)
    const identity = key(node)
    return identity && rect.width && rect.height && rect.right > bounds.left && rect.left < bounds.right
      && rect.bottom > top - METRIC_OVERSCAN_PX && rect.top < bottom + METRIC_OVERSCAN_PX ? [identity] : []
  }).slice(0, 199)
}

export function useVisibleMetricDemand(root: Ref<HTMLElement | null>, selector: string, key: (node: HTMLElement) => string | undefined,
  publish: (keys: string[]) => void, active: () => boolean = () => true) {
  const keys = ref<string[]>([])
  let frame: number | null = null
  let observer: ResizeObserver | null = null
  let signature = ''
  function measure() {
    frame = null
    const next = active() && root.value ? visibleMetricKeys(root.value, selector, key) : []
    const nextSignature = JSON.stringify(next)
    if (nextSignature === signature) return
    signature = nextSignature
    keys.value = next
    publish(next)
  }
  function schedule() { frame ??= requestAnimationFrame(measure) }
  onMounted(() => {
    root.value?.addEventListener('scroll', schedule, { capture: true, passive: true })
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(schedule)
      if (root.value) observer.observe(root.value)
    }
    schedule()
  })
  onUpdated(schedule)
  onBeforeUnmount(() => {
    if (frame !== null) cancelAnimationFrame(frame)
    observer?.disconnect()
    root.value?.removeEventListener('scroll', schedule, true)
    publish([])
  })
  return { keys, schedule }
}
