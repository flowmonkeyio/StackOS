import type { TrackerStatus, TrackerTask, TrackerTicket } from '@/lib/task-tracker/types'

export type ViewMode = 'graph' | 'tickets'
export type StatusFilter = 'all' | TrackerStatus
export type GraphBlockFilter = 'blocked' | 'open'
export type SelectMetaTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'accent'

export interface TaskProgressRow {
  id: number
  key: string
  task: TrackerTask
  tickets: TrackerTicket[]
  completedCount: number
  deferredCount: number
  abortedCount: number
  failedCount: number
  skippedCount: number
  terminalCount: number
  totalCount: number
  inProgressCount: number
  blockedCount: number
  percent: number
  workflowLabel: string
  currentDetail: string
}

export interface TrackerTaskSelectOption {
  value: string
  label: string
  rightLabel: string
  rightMeta: string
  rightTone: SelectMetaTone
}

export interface TrackerSelectOption {
  value: string
  label: string
}

export interface TrackerStatusOption {
  key: StatusFilter
  label: string
}

export interface TrackerViewOption {
  key: ViewMode
  label: string
  icon: string
}
