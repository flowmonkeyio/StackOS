import type { AgentStats } from '@/lib/agentActivity'

export function agentStatsFixture(overrides: Partial<AgentStats> = {}): AgentStats {
  const metrics = {
    launch_attempts: 4,
    recorded_reviewer_rejects: 1,
    reported_usage: { unit: 'tokens', input: 70, output: 30, total: 100 },
    coverage: {
      usage_status: 'complete', usage_measured_attempts: 4, usage_unavailable_attempts: 0,
      classification_status: 'complete', classification_known_attempts: 4, classification_unknown_attempts: 0,
    },
  }
  return {
    project_id: 1, is_active: null, as_of: '2026-11-06T20:00:00Z', totals: metrics,
    rows: [{ ...metrics, project_id: 1, project_name: 'Fixture project', project_slug: 'fixture-project', agent_name: 'same-name', selected_role: 'delivery_agent', host_key: 'codex', model: 'recorded-sol' }],
    ...overrides,
  } as AgentStats
}
