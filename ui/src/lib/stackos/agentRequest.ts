/**
 * Shared types + helpers for the agent-request inbox.
 *
 * Agent requests are operation-only (no REST router, so no generated `Schema*`
 * type), yet both the Inbox view and the attention aggregator need the shape.
 * This is the single source of truth — import from here instead of redefining.
 */

import { callOperation } from '@/lib/operations'

export type AgentRequestStatus =
  | 'new'
  | 'claimed'
  | 'run-created'
  | 'run-started'
  | 'responded'
  | 'resolved'
  | 'ignored'
  | 'failed'

export type AgentRequestAttentionStatus = 'unread' | 'read' | 'archived'

export interface AgentRequestOut {
  id: number
  project_id: number
  request_key: string
  title: string
  body_preview: string
  source_provider: string | null
  source_kind: string | null
  source_resource_key: string | null
  source_resource_record_id: number | null
  source_message_ref: string | null
  priority: number
  status: AgentRequestStatus
  attention_status: AgentRequestAttentionStatus
  claimed_by: string | null
  claimed_at: string | null
  claim_expires_at: string | null
  run_plan_id: number | null
  completed_at: string | null
  ignored_at: string | null
  metadata_json: Record<string, unknown> | null
  created_at: string
  updated_at: string
}

export interface AgentRequestPage {
  items: AgentRequestOut[]
  next_cursor: number | null
  total_estimate: number
}

export interface ListAgentRequestArgs {
  project_id: number
  statuses?: AgentRequestStatus[]
  attention_status?: AgentRequestAttentionStatus
  claimed_by?: string
  claimable?: boolean
  limit?: number
  after_id?: number | null
}

export function listAgentRequests(args: ListAgentRequestArgs): Promise<AgentRequestPage> {
  return callOperation<AgentRequestPage>('agentRequest.list', { ...args })
}
