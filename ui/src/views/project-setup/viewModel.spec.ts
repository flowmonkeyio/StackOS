import { expect, it } from 'vitest'
import type { SchemaSetupWorkflowOut } from '@/api'
import { setupActivationGaps, setupWorkflowReady, setupWorkflowStatus } from './viewModel'

const workflow = (patch: Partial<SchemaSetupWorkflowOut> = {}) => ({
  status: 'current', latest_status: 'success', requested_scope: 'ready_to_use',
  activation_status: 'verified', missing_activation_claims: [], ...patch,
} as SchemaSetupWorkflowOut)

it('uses current activation coverage and preserves source or report failures', () => {
  expect(setupWorkflowReady(workflow())).toBe(true)
  expect(setupWorkflowStatus(workflow())).toBe('ready')
  expect(setupWorkflowStatus(workflow({ latest_status: 'failed' }))).toBe('failed')
  expect(setupWorkflowStatus(workflow({ status: 'source_unavailable' as SchemaSetupWorkflowOut['status'], latest_status: 'failed' }))).toBe('source_unavailable')
  expect(setupWorkflowStatus(workflow({ missing_activation_claims: ['native_consumption'] }))).toBe('activation_pending')
})

it('labels historical preparation and blocked verification without inferring readiness', () => {
  expect(setupWorkflowStatus(workflow({ requested_scope: undefined, activation_status: undefined }))).toBe('prepared')
  expect(setupWorkflowStatus(workflow({ activation_status: 'blocked' as SchemaSetupWorkflowOut['activation_status'] }))).toBe('activation_blocked')
})

it('renders actionable gaps without exposing unknown machine keys', () => {
  expect(setupActivationGaps(workflow({ missing_activation_claims: ['saved_defaults', 'native_consumption', 'internal_future_claim', 'another_unknown'] }))).toEqual([
    'Save and verify the project’s agent settings.', 'Verify that the agent loaded the saved setup.', 'Verify the remaining workflow setup.',
  ])
})
