import type {
  SchemaTrackerReorderTaskInput,
  SchemaTrackerUpdateTaskPriorityInput,
  SchemaWriteEnvelopeTrackerTaskBoardOut,
} from '@/api'
import { callOperation } from '@/lib/operations'

// The caller owns the immutable intent, pending state and project-generation
// check. These calls submit once; the caller refreshes after a failed save.
export type TaskPriorityIntent = Omit<SchemaTrackerUpdateTaskPriorityInput,
  'run_token' | 'expected_etag' | 'response_mode' | 'priority_key' | 'expected_status'> & {
  priority_key: `${SchemaTrackerUpdateTaskPriorityInput['priority_key']}`
  expected_status: `${SchemaTrackerUpdateTaskPriorityInput['expected_status']}`
}
export type TaskReorderIntent = Omit<SchemaTrackerReorderTaskInput,
  'run_token' | 'expected_etag' | 'response_mode' | 'placement' | 'expected_status'> & {
  placement: `${SchemaTrackerReorderTaskInput['placement']}`
  expected_status: `${SchemaTrackerReorderTaskInput['expected_status']}`
}

export function saveTaskPriority(intent: TaskPriorityIntent): Promise<SchemaWriteEnvelopeTrackerTaskBoardOut> {
  return callOperation('tracker.updateTaskPriority', intent)
}

export function reorderTask(intent: TaskReorderIntent): Promise<SchemaWriteEnvelopeTrackerTaskBoardOut> {
  return callOperation('tracker.reorderTask', intent)
}
