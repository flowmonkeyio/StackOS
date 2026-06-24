import type { SchemaAuthProviderOut, SchemaCredentialConnectionOut } from '@/api'

export type ConnectionRow = SchemaCredentialConnectionOut & { id: string }
export type AuthMethod = NonNullable<SchemaAuthProviderOut['auth_methods']>[number]
export type AuthField = NonNullable<AuthMethod['fields']>[number]
export type MessageTone = 'success' | 'danger' | 'info'
export type ConnectionSection = 'services' | 'communications' | 'telegram' | 'diagnostics'

export interface ServiceGroup {
  provider: SchemaAuthProviderOut | null
  providerKey: string
  connections: ConnectionRow[]
}

export interface TelegramCommandSpec {
  command: string
  description?: string
  guidance?: string
  enabled?: boolean
  aliases?: string[]
  arguments_schema?: Record<string, unknown>
  required_context?: string[]
  expected_outputs?: string[]
}

export interface TelegramCommandDraft {
  command: string
  description: string
  guidance: string
  enabled: boolean
}

export interface TelegramProfileForm {
  key: string
  auth_profile_key: string
  bot_username: string
  identity_display_name: string
  identity_purpose: string
  identity_voice: string
  agent_default_instructions: string
  agent_boundaries: string
  agent_escalation: string
  allowed_chat_refs: string
  allowed_user_refs: string
  commands: TelegramCommandDraft[]
  mention_patterns: string
  store_non_trigger_messages: boolean
  origin_required: boolean
  reply_to_source_message: boolean
  same_thread: boolean
}

export interface CommunicationProfile {
  record_id: number
  project_id: number
  profile_ref: string
  key: string
  enabled: boolean
  identity: {
    display_name?: string
    purpose?: string
    voice?: string
  }
  agent_guidance: Record<string, unknown>
  provider_facets: Record<string, Record<string, unknown>>
  access_policy: {
    allowed_user_refs?: string[]
    denied_user_refs?: string[]
    allowed_chat_refs?: string[]
    denied_chat_refs?: string[]
    user_mode?: string
    dm_mode?: string
    channel_mode?: string
    group_mode?: string
  }
  trigger_policy: Record<string, unknown>
  visibility_policy: Record<string, unknown>
  context_policy: Record<string, unknown>
  response_policy: Record<string, unknown>
  send_policy: Record<string, unknown>
  handoff_policy: Record<string, unknown>
  approval_policy: Record<string, unknown>
  metadata_json: Record<string, unknown>
}

export interface CommunicationProfileListOut {
  items: CommunicationProfile[]
  next_cursor: string | number | null
  total_estimate: number | null
}

export interface CommunicationTarget {
  record_id: number
  project_id: number
  target_ref: string
  key: string
  display_name: string | null
  provider_key: string
  surface_ref: string
  profile_ref: string | null
  thread_ref: string | null
  enabled: boolean
  action_ref: string | null
  action_input_defaults: Record<string, unknown>
  send_policy: {
    mode?: string
    allowed_profile_refs?: string[]
    allowed_invoker_refs?: string[]
    allowed_source_surface_refs?: string[]
    allowed_target_refs?: string[]
    requires_approval?: boolean
  }
  metadata_json: Record<string, unknown>
}

export interface CommunicationTargetListOut {
  items: CommunicationTarget[]
  next_cursor: string | number | null
  total_estimate: number | null
}

export interface CommunicationSurface {
  record_id: number
  project_id: number
  surface_ref: string
  channel_ref: string
  provider_key: string
  kind: string
  display_name: string | null
  ingest_enabled: boolean
  send_enabled: boolean
  capabilities: Record<string, unknown>
  audience: string
  intent: Record<string, unknown>
  agent_guidance: Record<string, unknown>
  data_scope: Record<string, unknown>
  external_context: Record<string, unknown>
  metadata_json: Record<string, unknown>
}

export interface CommunicationSurfaceListOut {
  items: CommunicationSurface[]
  next_cursor: string | number | null
  total_estimate: number | null
}

export interface IngressEndpointRoute {
  provider_key: string
  profile_key: string
  profile_ref?: string
  ingress_url?: string
  local_url?: string
  remote_status?: string
  notes?: string[]
}

export interface IngressEndpointStatusOut {
  configured?: boolean
  ready?: boolean
  endpoint?: {
    driver?: string
    status?: string
    public_base_url?: string | null
    local_base_url?: string | null
  } | null
  routes?: IngressEndpointRoute[]
  notes?: string[]
}

export type MessageMap = Record<string, { tone: MessageTone; text: string }>
