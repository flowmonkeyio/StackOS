<script setup lang="ts" generic="T">
import { nextTick, onBeforeUnmount, onUpdated, ref } from 'vue'
import { changesOrder, type ReorderHandleBindings, type ReorderMove } from './verticalReorder'

const props = defineProps<{
  items: T[]
  itemKey: (item: T) => string
  itemLabel: (item: T) => string
  /** Only meaningful eligibility revisions; do not include decorative/live data. */
  itemVersion?: (item: T) => string
  scopeKey?: string
  disabled?: boolean
  /** Defaults to this list. Only this element is ever scrolled. */
  scrollContainer?: HTMLElement | null
}>()
const emit = defineEmits<{ move: [move: ReorderMove] }>()
const root = ref<HTMLElement | null>(null)
const preview = ref<HTMLElement | null>(null)
const gesture = ref<{ key: string; version?: string; scope?: string; height: number } | null>(null)
const insertion = ref<{ key: string; version?: string; placement: 'before' | 'after' } | null>(null)
const announcement = ref('')
let pointer: { x: number; y: number } | null = null
let frame: number | null = null
let lastTime = 0
let edgeSince = 0
let direction = 0
let focusKey: string | null = null
let scrollOwner: HTMLElement | null = null
const elements = () => Array.from(root.value?.querySelectorAll<HTMLElement>(':scope > [data-reorder-key]') ?? [])
const item = (key: string) => props.items.find(value => props.itemKey(value) === key)
const element = (key: string) => elements().find(value => value.dataset.reorderKey === key)
const scrollElement = () => props.scrollContainer ?? root.value

function valid(): boolean {
  const active = gesture.value
  if (!active || props.disabled || active.scope !== props.scopeKey) return false
  const source = item(active.key)
  const target = insertion.value ? item(insertion.value.key) : undefined
  return source !== undefined && props.itemVersion?.(source) === active.version
    && scrollElement() === scrollOwner
    && (!insertion.value || (target !== undefined && props.itemVersion?.(target) === insertion.value.version))
}
function stopScroll(): void {
  if (frame !== null) cancelAnimationFrame(frame)
  frame = null; lastTime = 0; edgeSince = 0; direction = 0
}
function clearFeedback(): void { pointer = null; insertion.value = null; stopScroll() }
function finish(cancelled = true): void {
  const active = gesture.value
  clearFeedback()
  gesture.value = null
  scrollOwner?.removeEventListener('scroll', scrollChanged)
  scrollOwner = null
  document.removeEventListener('dragover', outsideOver)
  document.removeEventListener('drop', outsideDrop, true)
  document.removeEventListener('dragend', dragEnd)
  document.removeEventListener('keydown', escape, true)
  document.removeEventListener('visibilitychange', visibility)
  window.removeEventListener('blur', dragEnd)
  if (active && cancelled) {
    const source = item(active.key)
    announcement.value = `${source === undefined ? 'Move' : props.itemLabel(source)} cancelled.`
  }
}
function dragEnd(): void { finish() }
function escape(event: KeyboardEvent): void {
  if (event.key !== 'Escape') return
  const key = gesture.value?.key
  finish(); event.preventDefault()
  if (key) restoreFocus(key)
}
function visibility(): void { if (document.hidden) finish() }
function outsideOver(event: DragEvent): void {
  if (!root.value?.contains(event.target as Node)) {
    clearFeedback()
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'none'
  }
}
function outsideDrop(event: DragEvent): void { if (!root.value?.contains(event.target as Node)) finish() }
function leave(event: DragEvent): void {
  if (root.value?.contains(event.relatedTarget as Node)) return
  // Native child leave events can omit relatedTarget. They bubble through the
  // list while its pointer is still inside; do not reset scrolling/feedback.
  const rect = root.value?.getBoundingClientRect()
  if (!event.relatedTarget && event.target !== root.value && rect
    && event.clientX >= rect.left && event.clientX <= rect.right
    && event.clientY >= rect.top && event.clientY <= rect.bottom) return
  clearFeedback()
}
function scrollChanged(): void {
  if (!valid()) { finish(); return }
  updateTarget()
}
function guardStart(event: DragEvent): void { if (!(event.target as HTMLElement).closest('[data-reorder-handle]')) event.preventDefault() }
function lift(event: DragEvent, value: T): void {
  const handle = event.currentTarget as HTMLElement
  if (props.disabled || !event.dataTransfer || !handle?.hasAttribute('data-reorder-handle')) { event.preventDefault(); return }
  finish(false)
  const key = props.itemKey(value)
  gesture.value = { key, version: props.itemVersion?.(value), scope: props.scopeKey, height: element(key)?.getBoundingClientRect().height ?? 0 }
  scrollOwner = scrollElement()
  scrollOwner?.addEventListener('scroll', scrollChanged)
  event.dataTransfer.effectAllowed = 'move'
  // Never trust transferred text as an identity or accept another list's drag.
  event.dataTransfer.setData('text/plain', key)
  if (preview.value) {
    preview.value.textContent = props.itemLabel(value)
    event.dataTransfer.setDragImage(preview.value, 16, 8)
  }
  announcement.value = `${props.itemLabel(value)} lifted. Drop within this list, or press Escape to cancel.`
  document.addEventListener('dragover', outsideOver)
  document.addEventListener('drop', outsideDrop, true)
  document.addEventListener('dragend', dragEnd)
  document.addEventListener('keydown', escape, true)
  document.addEventListener('visibilitychange', visibility)
  window.addEventListener('blur', dragEnd)
}
function updateTarget(): void {
  if (!pointer || !gesture.value) return
  const candidates = elements().filter(node => node.dataset.reorderKey !== gesture.value!.key)
  const node = candidates.find(node => {
    const rect = node.getBoundingClientRect()
    return pointer!.y < rect.top + rect.height / 2
  }) ?? candidates.at(-1)
  const target = node?.dataset.reorderKey ? item(node.dataset.reorderKey) : undefined
  if (!node || target === undefined) { insertion.value = null; return }
  const rect = node.getBoundingClientRect()
  const placement = pointer.y < rect.top + rect.height / 2 ? 'before' : 'after'
  const move = { itemKey: gesture.value.key, anchorKey: props.itemKey(target), placement } as const
  insertion.value = changesOrder(props.items.map(props.itemKey), move)
    ? { key: move.anchorKey, version: props.itemVersion?.(target), placement } : null
}
function edge(): number {
  const container = scrollElement()
  if (!container || !pointer) return 0
  const rect = container.getBoundingClientRect()
  if (pointer.x < rect.left || pointer.x > rect.right || pointer.y < rect.top || pointer.y > rect.bottom) return 0
  const band = Math.min(64, rect.height / 4)
  if (band <= 0) return 0
  if (pointer.y < rect.top + band && container.scrollTop > 0) return -Math.pow(1 - (pointer.y - rect.top) / band, 2)
  if (pointer.y > rect.bottom - band && container.scrollTop < container.scrollHeight - container.clientHeight) return Math.pow(1 - (rect.bottom - pointer.y) / band, 2)
  return 0
}
function scrollTick(time: number): void {
  frame = null
  if (!valid()) { finish(); return }
  const amount = edge()
  if (!amount) { stopScroll(); return }
  if (direction !== Math.sign(amount)) { direction = Math.sign(amount); edgeSince = time; lastTime = time }
  const elapsed = Math.min(32, Math.max(0, time - lastTime))
  const speed = 120 + 480 * Math.min(1, (time - edgeSince) / 400)
  lastTime = time
  const container = scrollElement()!
  container.scrollTop = Math.max(0, Math.min(container.scrollHeight - container.clientHeight, container.scrollTop + amount * speed * elapsed / 1000))
  updateTarget()
  if (edge()) frame = requestAnimationFrame(scrollTick)
  else stopScroll()
}
function over(event: DragEvent): void {
  if (!gesture.value) return
  if (!valid()) { finish(); return }
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'move'
  pointer = { x: event.clientX, y: event.clientY }
  updateTarget()
  if (!edge()) stopScroll()
  else if (frame === null) frame = requestAnimationFrame(scrollTick)
}
function restoreFocus(key: string): void {
  focusKey = key
  void nextTick(() => {
    const node = element(key)
    const handle = node?.querySelector<HTMLButtonElement>('[data-reorder-handle]')
    if (!node || !handle) { focusKey = null; return }
    if (handle.disabled) node.focus({ preventScroll: true })
    else { handle.focus({ preventScroll: true }); focusKey = null }
    const container = scrollElement()
    if (!container) return
    const bounds = container.getBoundingClientRect()
    const rect = node.getBoundingClientRect()
    if (rect.top < bounds.top) container.scrollTop -= bounds.top - rect.top
    else if (rect.bottom > bounds.bottom) container.scrollTop += rect.bottom - bounds.bottom
  })
}
function requestMove(move: ReorderMove): void {
  if (props.disabled || !changesOrder(props.items.map(props.itemKey), move)) return
  const source = item(move.itemKey)!
  const target = item(move.anchorKey)!
  emit('move', move)
  announcement.value = `Moved ${props.itemLabel(source)} ${move.placement} ${props.itemLabel(target)}.`
  restoreFocus(move.itemKey)
}
function drop(event: DragEvent): void {
  if (!valid()) { finish(); return }
  over(event) // Recalculate against the actual release coordinates.
  const source = gesture.value?.key
  const target = insertion.value
  finish(false)
  if (source && target) requestMove({ itemKey: source, anchorKey: target.key, placement: target.placement })
  else announcement.value = 'Order unchanged.'
}
function handle(value: T): ReorderHandleBindings {
  const key = props.itemKey(value)
  const index = props.items.findIndex(item => props.itemKey(item) === key)
  return {
    label: `Move ${props.itemLabel(value)}`, disabled: Boolean(props.disabled),
    onLift: event => lift(event, value), onFinish: dragEnd,
    items: [
      { key: 'up', label: 'Move up', anchor: index - 1, placement: 'before' as const, disabled: index <= 0 },
      { key: 'down', label: 'Move down', anchor: index + 1, placement: 'after' as const, disabled: index >= props.items.length - 1 },
      { key: 'top', label: 'Move to top', anchor: 0, placement: 'before' as const, disabled: index <= 0 },
      { key: 'bottom', label: 'Move to bottom', anchor: props.items.length - 1, placement: 'after' as const, disabled: index >= props.items.length - 1 },
    ].map(action => ({ ...action, disabled: props.disabled || action.disabled, onSelect: () => {
      const target = props.items[action.anchor]
      if (target) requestMove({ itemKey: key, anchorKey: props.itemKey(target), placement: action.placement })
    } })),
  }
}
onUpdated(() => {
  if (gesture.value && !valid()) finish()
  if (focusKey && !props.disabled) restoreFocus(focusKey)
})
onBeforeUnmount(() => { finish(false); focusKey = null })
</script>

<template>
  <div
    ref="root"
    class="ui-reorder-list"
    @dragover="over"
    @drop="drop"
    @dragleave="leave"
    @dragstart="guardStart"
  >
    <div
      v-for="(value, index) in items"
      :key="itemKey(value)"
      :data-reorder-key="itemKey(value)"
      tabindex="-1"
      class="ui-reorder-list__item focus-ring"
      :class="{ 'ui-reorder-list__first': index === 0, 'ui-reorder-list__last': index === items.length - 1, 'ui-reorder-list__placeholder': gesture?.key === itemKey(value), 'ui-reorder-list__before': insertion?.key === itemKey(value) && insertion.placement === 'before', 'ui-reorder-list__after': insertion?.key === itemKey(value) && insertion.placement === 'after' }"
      :style="gesture?.key === itemKey(value) && gesture.height ? { height: `${gesture.height}px` } : undefined"
    >
      <slot
        :item="value"
        :handle="handle(value)"
      />
    </div>
    <slot
      v-if="!items.length"
      name="empty"
    />
    <span
      class="sr-only"
      role="status"
    >{{ announcement }}</span>
    <div
      ref="preview"
      class="ui-reorder-list__preview"
      aria-hidden="true"
    />
  </div>
</template>

<style scoped>
.ui-reorder-list { display: grid; grid-template-columns: minmax(0, 1fr); align-content: start; gap: var(--reorder-gap, 8px); overflow-y: auto; overscroll-behavior: contain; min-height: 0; }
.ui-reorder-list__item { position: relative; min-width: 0; box-sizing: border-box; }
.ui-reorder-list__placeholder { opacity: .4; }
.ui-reorder-list__before::before, .ui-reorder-list__after::after { content: ''; position: absolute; z-index: 1; left: 0; right: 0; height: 2px; background: var(--color-border-focus); pointer-events: none; }
.ui-reorder-list__before::before { top: calc(var(--reorder-gap, 8px) / -2); }
.ui-reorder-list__after::after { bottom: calc(var(--reorder-gap, 8px) / -2); }
/* Boundary lines stay inside the items so the scroll viewport cannot clip them. */
.ui-reorder-list__first.ui-reorder-list__before::before { top: 0; }
.ui-reorder-list__last.ui-reorder-list__after::after { bottom: 0; }
.ui-reorder-list__preview { position: fixed; left: -10000px; top: 0; max-width: 280px; padding: 8px 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--color-fg-strong); background: var(--color-bg-surface); border: 1px solid var(--color-border-default); border-radius: var(--radius-lg); pointer-events: none; }
</style>
