import { AgentStatsCoverageOutClassification_status as Classification, AgentStatsCoverageOutUsage_status as Usage, AgentStatsFieldCoverageOutStatus as Field, AgentStatsRowOutSelected_roleAnyOf0 as Role, TrackerAgentStatsOutGroup_by as Group } from '@/api'
import type { ActivityGroup, AgentStats, AgentStatsMetrics, AgentStatsRow } from '@/lib/agentActivity'

export function agentStatsFixture(overrides: Partial<AgentStats> = {}): AgentStats {
  const metrics: AgentStatsMetrics = {
    last_execution: null,
    launch_attempts: 4,
    recorded_reviewer_rejects: 1,
    reported_usage: { unit: 'tokens', input: 70, output: 30, total: 100, cache_read: 40, cache_write: 10, reasoning: 5, remaining_input: 30, input_with_unknown_cache: 0 },
    review_outcomes: { success: 1, repair: 1 },
    coverage: {
      usage_status: Usage.complete, usage_measured_attempts: 4, usage_unavailable_attempts: 0,
      classification_status: Classification.complete, classification_known_attempts: 4, classification_unknown_attempts: 0,
      fields: Object.fromEntries(['input', 'output', 'total', 'cache_read', 'cache_write', 'reasoning'].map(key => [key, { status: Field.complete, measured_attempts: 4, unavailable_attempts: 0 }])),
    },
  }
  const row: AgentStatsRow = { ...structuredClone(metrics), group_key: 'fixture-model', project_ids: [1], project_id: 1, project_name: 'Fixture project', project_slug: 'fixture-project', agent_name: 'same-name', selected_role: Role.delivery_agent, host_key: 'codex', model: 'recorded-sol' }
  const empty = structuredClone(metrics)
  empty.launch_attempts = 0
  empty.recorded_reviewer_rejects = 0
  empty.review_outcomes = {}
  empty.reported_usage = { unit: 'tokens', input: 0, output: 0, total: 0, cache_read: 0, cache_write: 0, reasoning: 0, remaining_input: 0, input_with_unknown_cache: 0 }
  empty.coverage = { usage_status: Usage.empty, usage_measured_attempts: 0, usage_unavailable_attempts: 0, classification_status: Classification.empty, classification_known_attempts: 0, classification_unknown_attempts: 0, fields: Object.fromEntries(['input', 'output', 'total', 'cache_read', 'cache_write', 'reasoning'].map(key => [key, { status: Field.empty, measured_attempts: 0, unavailable_attempts: 0 }])) }
  return {
    project_id: 1, is_active: null, as_of: '2026-11-06T20:00:00Z', totals: metrics,
    rows: [row], group_by: Group.model, start_date: '2026-11-01', end_date: '2026-11-07', timezone: 'America/Los_Angeles',
    daily: [{ ...structuredClone(metrics), date: '2026-11-06', by_agent: [{ ...structuredClone(row), group_key: 'fixture-agent' }], by_model: [structuredClone(row)], by_agent_model: [{ ...structuredClone(row), group_key: 'fixture-agent-model' }] }], undated: empty,
    ...overrides,
  }
}

// Three recorded identities: one agent uses two exact versions, another shares one.
export function groupedAgentStatsFixture(group: `${ActivityGroup}`): AgentStats {
  const fixture = agentStatsFixture({ group_by: group as Group })
  function row(key: string, agent: string | null, model: string | null, total: number): AgentStatsRow {
    return { ...structuredClone(fixture.rows[0]!), group_key: key, agent_name: agent, model,
      reported_usage: { ...fixture.totals.reported_usage, input: total, remaining_input: total, cache_read: 0, output: 0, total } }
  }
  const by_agent = [row('agent-a', 'Agent A', null, 300), row('agent-b', 'Agent B', 'gpt-6.1-sol', 300)]
  const by_model = [row('model-61', null, 'gpt-6.1-sol', 400), row('model-6', null, 'gpt-6-sol', 200)]
  const by_agent_model = [row('a-61', 'Agent A', 'gpt-6.1-sol', 100), row('a-6', 'Agent A', 'gpt-6-sol', 200), row('b-61', 'Agent B', 'gpt-6.1-sol', 300)]
  fixture.daily[0] = { ...fixture.daily[0]!, ...row('total', null, null, 600), by_agent, by_model, by_agent_model }
  fixture.rows = structuredClone(group === 'model' ? by_model : group === 'agent_model' ? by_agent_model : by_agent)
  fixture.totals = structuredClone(fixture.daily[0]!)
  return fixture
}
