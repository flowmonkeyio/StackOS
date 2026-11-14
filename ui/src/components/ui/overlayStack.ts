import { zIndex } from '@/design/tokens'

export interface OverlayEntry {
  layer: number
  returnFocus: HTMLElement | null
  element: () => HTMLElement | null
  lockScroll: boolean
  parent: OverlayEntry | null
}

const stack: OverlayEntry[] = []
let previousBodyOverflow = ''

/** Shared opening order, logical portal ownership and modal scroll locking. */
export function openOverlay(
  element: OverlayEntry['element'],
  lockScroll = true,
  opener = document.activeElement instanceof HTMLElement ? document.activeElement : null,
): OverlayEntry {
  if (lockScroll && !stack.some(entry => entry.lockScroll)) {
    previousBodyOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
  }
  const entry: OverlayEntry = {
    layer: (stack.at(-1)?.layer ?? zIndex.overlay) + 2,
    returnFocus: opener,
    element,
    lockScroll,
    parent: [...stack].reverse().find(entry => opener && entry.element()?.contains(opener)) ?? null,
  }
  stack.push(entry)
  return entry
}

export function isTopOverlay(entry: OverlayEntry | null): boolean {
  return entry !== null && stack.at(-1) === entry
}

/** A portaled child is inside its parent for outside-pointer handling. */
export function overlayContains(entry: OverlayEntry | null, target: Node): boolean {
  if (!entry) return false
  return Boolean(entry.element()?.contains(target))
    || stack.some(child => child.parent === entry && overlayContains(child, target))
}

export function closeOverlay(entry: OverlayEntry | null): void {
  if (!entry) return
  const index = stack.indexOf(entry)
  if (index < 0) return
  const wasTop = isTopOverlay(entry)
  // A lower overlay may unmount before its child; keep the final return target alive.
  for (const later of stack.slice(index + 1)) {
    if (later.returnFocus && entry.element()?.contains(later.returnFocus)) later.returnFocus = entry.returnFocus
    if (later.parent === entry) later.parent = entry.parent
  }
  stack.splice(index, 1)
  if (entry.lockScroll && !stack.some(item => item.lockScroll)) document.body.style.overflow = previousBodyOverflow
  if (wasTop && entry.returnFocus?.isConnected) entry.returnFocus.focus({ preventScroll: true })
}
