import { describe, expect, it } from 'vitest'
import type { TrackerSnapshot } from '@/lib/task-tracker/types'
import { currentFocusedDetail } from './detailUtils'

const focused = {
  tracker: { project_id: 7, rev: 12 },
  tasks: [{ key: 'owned', context_json: { primary: 'Retain every requested field' }, definition_of_done_json: ['proof'] }],
  tickets: [{ context_json: { evidence: 'Retain complete primary ticket content' } }],
  graph: { nodes: [], edges: [] },
} as unknown as TrackerSnapshot

describe('current focused detail', () => {
  it('reuses complete primary objects without serialization or reconstruction', () => {
    const detail = currentFocusedDetail(focused, 7, 'owned', 12)!
    expect(detail.tasks).toBe(focused.tasks)
    expect(detail.tickets).toBe(focused.tickets)
    expect(detail.graph).toBeNull()
  })
  it('requires the current full project/task/revision contract', () => {
    expect(currentFocusedDetail(null, 7, 'owned', 12)).toBeNull()
    expect(currentFocusedDetail(focused, 8, 'owned', 12)).toBeNull()
    expect(currentFocusedDetail(focused, 7, 'foreign', 12)).toBeNull()
    expect(currentFocusedDetail(focused, 7, 'owned', 13)).toBeNull()
    expect(currentFocusedDetail({ ...focused, projection: 'ui' }, 7, 'owned', 12)).toBeNull()
  })
})
