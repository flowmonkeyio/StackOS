import type { SchemaTaskResultItem } from '@/api'
import { useTaskInspectionPage, type TaskInspectionScope } from './useTaskInspectionPage'

export function useTaskResults(scope: TaskInspectionScope) {
  return useTaskInspectionPage<SchemaTaskResultItem>(scope, 'tracker.taskResults', 20)
}
