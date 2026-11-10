import type { SchemaTaskActivityItem } from '@/api'
import { useTaskInspectionPage, type TaskInspectionScope } from './useTaskInspectionPage'

export function useTaskActivity(scope: TaskInspectionScope) {
  return useTaskInspectionPage<SchemaTaskActivityItem>(scope, 'tracker.taskActivity', 3)
}
