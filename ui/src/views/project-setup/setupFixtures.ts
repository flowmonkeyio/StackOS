import type { SchemaProjectSetupOut, SchemaSetupAttempt } from '@/api'
import { ProjectSetupOutFolder_status, ProjectSetupOutStatus } from '@/api'

export function setupFixture(patch: Partial<SchemaProjectSetupOut> = {}): SchemaProjectSetupOut {
  return {
    project_id: 1, eligible: true, folder_status: ProjectSetupOutFolder_status.available,
    status: ProjectSetupOutStatus.setup_needed, binding_id: 1, project_folder_issue: null,
    configured_count: 0, unknown_count: 0, update_count: 0, current_attempt: null,
    setup_reminder_dismissed_at: null, update_reminder_dismissed_at: null,
    launch_preferences: {},
    delivery_defaults_digest: '44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a',
    workflows: [], detail_ref: {}, next_action: 'inspect_setup',
    notice: 'Set up this project', attempt_diagnostic: null, ...patch,
  }
}

export function attemptFixture(patch: Partial<SchemaSetupAttempt> = {}): SchemaSetupAttempt {
  return {
    schema_version: 1, project_id: 1, attempt_ref: 'setup-attempt:1:10',
    root_request_id: 10, request_id: 10, revision: 1, mode: 'setup', requested_scope: 'ready_to_use', entry_kind: 'managed_ui',
    session_id: null, runtime: 'codex', workflow_keys: [], observed_receipt_revisions: {},
    status: 'queued', predecessor_request_id: null, successor_request_id: null, thread_id: null,
    managed: null, question: null, progress: null, recovery_condition: null, outcomes: {},
    reports: [], answers: [], remaining_workflow_keys: [], revalidate_workflow_keys: [],
    partial_changes_possible: false, ...patch,
  } as SchemaSetupAttempt
}

export const setupHosts = { items: [{
  host_key: 'codex', display_name: 'Codex', available: true,
  agent_execution: { sandboxes: ['read-only', 'workspace-write'], models: ['catalog-model', 'second-model'], recommended_model: 'catalog-model', custom_models: true, provider_selection: false, profile_selection: false, requires_provider_model: false },
}, { host_key: 'desktop-only', display_name: 'Desktop app', available: true, agent_execution: { sandboxes: [], models: [] } }] }
