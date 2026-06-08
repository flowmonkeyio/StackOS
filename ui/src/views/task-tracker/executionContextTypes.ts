export interface TaskExecutionContextLink {
  id: number
  link_type: string
  link_ref: string
  role: string
}

export interface TaskExecutionContext {
  id: number
  context_ref: string
  name: string
  description?: string | null
  plugin_slug?: string | null
  provider_key?: string | null
  action_ref?: string | null
  credential_ref?: string | null
  provider_context_json?: Record<string, unknown>
  output_policy_json?: Record<string, unknown>
  request_budget_json?: Record<string, unknown>
  artifact_namespace?: string | null
  status: string
  links?: TaskExecutionContextLink[]
  artifact_count?: number
}

export interface TaskExecutionContextPage {
  items: TaskExecutionContext[]
  next_cursor: number | null
  total_estimate: number
}

export interface TaskExecutionContextPageInfo {
  limit: number
  nextCursor: number | null
  totalEstimate: number
}

export type TaskExecutionContextArtifactPageInfo = Record<string, TaskExecutionContextPageInfo>

export interface TaskExecutionContextArtifact {
  id: number
  context_ref: string
  artifact_id: number
  action_call_id: number | null
  semantic_name: string | null
  action_ref: string | null
  input_hash?: string | null
  metadata_json: Record<string, unknown> | null
  created_at: string
  artifact: Record<string, unknown>
}

export interface TaskExecutionContextArtifactPage {
  items: TaskExecutionContextArtifact[]
  next_cursor: number | null
  total_estimate: number
}
