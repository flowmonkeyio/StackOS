import type { TrackerSnapshot } from '@/lib/task-tracker/types'

export function hasJsonObject(value: Record<string, unknown> | null): boolean {
  return Boolean(value && Object.keys(value).length > 0)
}

export function formatJsonBlock(value: Record<string, unknown> | null): string {
  return value ? JSON.stringify(value, null, 2) : ''
}

/** Reuse primary content only when the full focused read covers current project truth. */
export function currentFocusedDetail(
  focused: TrackerSnapshot | null, projectId: number, taskKey: string, revision: number,
): TrackerSnapshot | null {
  if (!focused || focused.projection === 'ui' || focused.tracker.project_id !== projectId
    || focused.tracker.rev < revision || focused.tasks.length !== 1
    || focused.tasks[0]?.key !== taskKey || !('context_json' in focused.tasks[0])
    || focused.tickets.some(ticket => !('context_json' in ticket))) return null
  return { ...focused, graph: null }
}
