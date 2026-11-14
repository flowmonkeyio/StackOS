import type { SchemaSetupWorkflowOut } from '@/api'

export function setupWorkflowReady(workflow: SchemaSetupWorkflowOut): boolean {
  return workflow.status === 'current' && (!workflow.latest_status || workflow.latest_status === 'success')
    && workflow.requested_scope === 'ready_to_use' && workflow.activation_status === 'verified'
    && !workflow.missing_activation_claims?.length
}

// ProjectSetup.get owns freshness. These labels only project its current receipt;
// historical preparation or a successful report alone cannot establish readiness.
export function setupWorkflowStatus(workflow: SchemaSetupWorkflowOut): string {
  if (workflow.status === 'source_unavailable') return workflow.status
  if (workflow.latest_status && workflow.latest_status !== 'success') return workflow.latest_status
  if (setupWorkflowReady(workflow)) return 'ready'
  if (workflow.activation_status === 'blocked') return 'activation_blocked'
  if (workflow.status === 'current' || workflow.status === 'activation_pending') {
    return workflow.requested_scope === 'ready_to_use' ? 'activation_pending' : 'prepared'
  }
  return workflow.status
}

export function setupActivationGaps(workflow: SchemaSetupWorkflowOut): string[] {
  const labels: Record<string, string> = {
    saved_defaults: 'Save and verify the project’s agent settings.',
    native_consumption: 'Verify that the agent loaded the saved setup.',
    current_environment: 'Restore the project folder and verify setup again.',
    current_sources: 'Check the updated workflow guidance.',
    current_activation_evidence: 'Recheck workflow readiness against the current setup.',
  }
  return [...new Set((workflow.missing_activation_claims ?? []).map(key => labels[key] ?? 'Verify the remaining workflow setup.'))]
}
