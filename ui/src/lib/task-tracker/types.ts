export type TrackerStatus =
  | 'not-started'
  | 'in-progress'
  | 'complete'
  | 'deferred'
  | 'aborted'
  | 'failed'
  | 'skipped'
export type TrackerNodeKind = 'task' | 'ticket' | 'group'
export type TrackerEdgeKind = 'contains' | 'dependency' | 'link'

export interface TrackerSummary {
  id: number
  project_id: number
  key: string
  name: string
  description: string
  rev: number
  created_at: string
  updated_at: string
}

export interface TrackerLane {
  key: string
  label: string
  position: number
}

export interface TrackerPriority {
  key: string
  label: string
  rank: number
  position: number
}

export interface TrackerTask {
  id: number
  project_id: number
  tracker_id: number
  key: string
  title: string
  goal: string
  description: string
  status: TrackerStatus
  priority_key: string
  lane_key: string
  owner: string | null
  task_type: string
  order_index: number
  source_kind: string
  source_json: Record<string, unknown> | null
  definition_of_done_json: string[]
  constraints_json: string[]
  expected_outcomes_json: string[]
  completion_evidence_json: Record<string, unknown> | null
  context_json: Record<string, unknown> | null
  metadata_json: Record<string, unknown> | null
  created_by: string | null
  created_at: string
  updated_at: string
  started_at: string | null
  completed_at: string | null
  ticket_summary?: TrackerTaskTicketSummary | null
}

export interface TrackerTaskTicketSummary {
  total_count: number
  terminal_count: number
  in_progress_count: number
  blocked_count: number
  status_counts: Record<string, number>
  assignees: string[]
  search_text: string
}

export interface TrackerTicket {
  id: number
  project_id: number
  tracker_id: number
  task_id: number
  task_key: string
  parent_ticket_id: number | null
  parent_ticket_key: string | null
  run_plan_id: number | null
  run_plan_step_id: number | null
  run_id: number | null
  agent_request_id: number | null
  key: string
  title: string
  goal: string
  status: TrackerStatus
  kind: 'ticket' | 'group'
  assignee: string | null
  priority_key: string
  lane_key: string
  order_index: number
  blocker_reason: string | null
  outcome: string | null
  effort: string | null
  source_kind: string
  source_json: Record<string, unknown> | null
  definition_of_done_json: string[]
  constraints_json: string[]
  expected_changes_json: string[]
  allowed_paths_json: string[]
  completion_evidence_json: Record<string, unknown> | null
  context_json: Record<string, unknown> | null
  metadata_json: Record<string, unknown> | null
  created_by: string | null
  claimed_at: string | null
  created_at: string
  updated_at: string
  started_at: string | null
  completed_at: string | null
  dependency_keys: string[]
  blocked_by: string[]
  reference_count: number
  link_count: number
}

export interface TrackerDependency {
  id: number
  ticket_key: string
  depends_on_ticket_key: string
  dependency_type: string
  metadata_json?: Record<string, unknown> | null
}

export interface TrackerLink {
  id: number
  task_id: number | null
  ticket_id: number | null
  link_kind: string
  ref: string | null
  run_plan_id: number | null
  run_plan_step_id: number | null
  run_id: number | null
  agent_request_id: number | null
  resource_record_id: number | null
  artifact_id: number | null
  action_call_id: number | null
  title: string | null
  metadata_json: Record<string, unknown> | null
  created_at: string
}

export interface TrackerGraphNode {
  id: string
  type: TrackerNodeKind
  parent_id: string | null
  label: string
  status: string
  lane_key: string
  priority_key: string
  data: Record<string, unknown>
}

export interface TrackerGraphEdge {
  id: string
  type: TrackerEdgeKind
  source: string
  target: string
  label: string | null
  data: Record<string, unknown>
}

export interface TrackerGraph {
  nodes: TrackerGraphNode[]
  edges: TrackerGraphEdge[]
  warnings: string[]
  layout_hints: Record<string, unknown>
}

export interface TrackerSnapshot {
  tracker: TrackerSummary
  lanes: TrackerLane[]
  priorities: TrackerPriority[]
  tasks: TrackerTask[]
  tickets: TrackerTicket[]
  dependencies: TrackerDependency[]
  links: TrackerLink[]
  graph: TrackerGraph | null
}

export interface TrackerSelectedItem {
  kind: 'task' | 'ticket'
  key: string
}
