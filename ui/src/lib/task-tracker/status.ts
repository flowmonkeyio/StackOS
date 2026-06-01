import type { TrackerStatus } from './types'

export const TRACKER_TERMINAL_STATUSES = new Set<TrackerStatus>([
  'complete',
  'deferred',
  'aborted',
  'failed',
  'skipped',
])

export function isTerminalTrackerStatus(status: TrackerStatus | string): boolean {
  return TRACKER_TERMINAL_STATUSES.has(status as TrackerStatus)
}

export function trackerStatusSortOrder(status: TrackerStatus | string): number {
  switch (status) {
    case 'in-progress':
      return 0
    case 'not-started':
      return 1
    case 'complete':
      return 2
    case 'deferred':
      return 3
    case 'skipped':
      return 4
    case 'aborted':
      return 5
    case 'failed':
      return 6
    default:
      return 7
  }
}
