import { TrackerItemStatus, ActiveTasksOutScope, TaskDeliveryStateOutStatus } from '@/api'
import type { SchemaActiveTaskOut, SchemaActiveTasksOut } from '@/api'
import { agentStatsFixture } from './agentStatsFixtures'

export function activeTaskFixture(overrides: Partial<SchemaActiveTaskOut> = {}): SchemaActiveTaskOut {
  return {
    id: 1, project_id: 1, key: 'active-work', title: 'Prepare delivery', goal: 'Recorded task goal',
    status: TrackerItemStatus.in_progress, priority_key: 'p1', owner: 'Operator',
    project: { id: 1, name: 'Business One', slug: 'business-one', domain: '', is_active: true, updated_at: '2026-11-01T00:00:00Z' },
    created_at: '2026-11-01T00:00:00Z', updated_at: '2026-11-01T00:00:00Z',
    started_at: '2026-11-01T00:00:00Z', completed_at: null,
    delivery: { status: TaskDeliveryStateOutStatus.queued, ticketKey: 'child', reason: null },
    active_execution: null, agent_stats: agentStatsFixture().totals, work_duration: null,
    ticket_summary: { total_count: 7, terminal_count: 2, blocked_count: 0, in_progress_count: 1, search_text: '', status_counts: { complete: 2 } },
    ...overrides,
  }
}

export function activeTasksFixture(projectId: number | null = null, items = [activeTaskFixture()]): SchemaActiveTasksOut {
  return { items, scope: projectId === null ? ActiveTasksOutScope.global : ActiveTasksOutScope.project,
    project_id: projectId, next_cursor: null, as_of: '2026-11-11T19:00:00Z' }
}
