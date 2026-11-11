import type { DropdownItem } from './UiDropdownMenu.vue'

export interface ReorderMove { itemKey: string; anchorKey: string; placement: 'before' | 'after' }
export interface ReorderHandleBindings {
  label: string
  disabled: boolean
  items: DropdownItem[]
  onLift: (event: DragEvent) => void
  onFinish: () => void
}

/** A neighbor intent changes order only when it selects a different list slot. */
export function changesOrder(keys: string[], move: ReorderMove): boolean {
  const source = keys.indexOf(move.itemKey)
  const target = keys.indexOf(move.anchorKey)
  if (source < 0 || target < 0 || source === target) return false
  const destination = target + (move.placement === 'after' ? 1 : 0) - (source < target ? 1 : 0)
  return destination !== source
}
