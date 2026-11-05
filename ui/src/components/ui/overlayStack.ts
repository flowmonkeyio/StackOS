import { zIndex } from '@/design/tokens'

export interface OverlayEntry {
  layer: number
  returnFocus: HTMLElement | null
  element: () => HTMLElement | null
}

const stack: OverlayEntry[] = []
let previousBodyOverflow = ''

/** Dialogs and drawers share opening order, keyboard ownership and one scroll lock. */
export function openOverlay(element: OverlayEntry['element']): OverlayEntry {
  if (!stack.length) {
    previousBodyOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
  }
  const entry: OverlayEntry = {
    layer: (stack.at(-1)?.layer ?? zIndex.overlay) + 2,
    returnFocus: document.activeElement instanceof HTMLElement ? document.activeElement : null,
    element,
  }
  stack.push(entry)
  return entry
}

export function isTopOverlay(entry: OverlayEntry | null): boolean {
  return entry !== null && stack.at(-1) === entry
}

export function closeOverlay(entry: OverlayEntry | null): void {
  if (!entry) return
  const index = stack.indexOf(entry)
  if (index < 0) return
  const wasTop = isTopOverlay(entry)
  // A lower overlay may unmount before its child; keep the final return target alive.
  for (const later of stack.slice(index + 1)) {
    if (later.returnFocus && entry.element()?.contains(later.returnFocus)) later.returnFocus = entry.returnFocus
  }
  stack.splice(index, 1)
  if (!stack.length) document.body.style.overflow = previousBodyOverflow
  if (wasTop && entry.returnFocus?.isConnected) entry.returnFocus.focus({ preventScroll: true })
}
