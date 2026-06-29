import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import {
  ActionCallStatus,
  ApprovalRequestStatus,
  RunPlanConsistencyIssueOutSeverity,
  RunPlanStatus,
  RunPlanStepStatus,
  type SchemaActionCallAuditOut,
  type SchemaRunPlanOut,
} from '@/api'
import RunPlanRenderer from './RunPlanRenderer.vue'

describe('RunPlanRenderer', () => {
  it('renders run-plan steps and redacted action calls', () => {
    const plan: SchemaRunPlanOut = {
      id: 1,
      project_id: 1,
      run_id: 22,
      template_id: null,
      template_version_id: null,
      context_snapshot_id: null,
      key: 'demo.run',
      title: 'Demo Run',
      goal: 'Execute explicit steps.',
      status: RunPlanStatus.started,
      template_key: 'demo.template',
      template_version: '0.1.0',
      template_source: 'plugin',
      template_origin_path: null,
      template_snapshot_json: null,
      inputs_json: {},
      selected_context_json: null,
      context_filters_json: null,
      grant_snapshot_json: null,
      budget_snapshot_json: null,
      policy_snapshot_json: null,
      output_contract_json: null,
      metadata_json: null,
      created_by: 'agent',
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      started_at: '2026-01-01T00:01:00Z',
      completed_at: null,
      steps: [
        {
          id: 10,
          run_plan_id: 1,
          step_id: 'write',
          title: 'Write',
          purpose: 'Write a record.',
          position: 0,
          status: RunPlanStepStatus.running,
          depends_on_json: [],
          input_refs_json: [],
          context_refs_json: [],
          action_refs_json: ['utils.image.generate'],
          resource_refs_json: [],
          policy_refs_json: [],
          approval_refs_json: [],
          output_refs_json: ['artifact'],
          instructions_json: [],
          success_criteria_json: [],
          action_payloads_json: null,
          expected_outputs_json: null,
          result_json: null,
          metadata_json: null,
          allowed_tools: ['action.execute'],
          error: null,
          claimed_by: 'agent',
          claimed_at: '2026-01-01T00:01:00Z',
          started_at: '2026-01-01T00:01:00Z',
          completed_at: null,
          created_at: '2026-01-01T00:00:00Z',
          updated_at: '2026-01-01T00:01:00Z',
        },
      ],
      approval_requests: [],
      consistency_issues: [],
    }
    const actionCalls: SchemaActionCallAuditOut[] = [
      {
        id: 50,
        project_id: 1,
        run_id: 22,
        run_plan_id: 1,
        run_plan_step_id: 10,
        action_key: 'image.generate',
        plugin_slug: 'utils',
        provider_key: 'openai-images',
        connector_key: 'openai-images',
        operation: 'image.generate',
        status: ActionCallStatus.success,
        dry_run: false,
        credential_ref: 'cred_123',
        request_json: { prompt: 'x', api_key: 'secret' },
        provider_context_json: null,
        response_json: { uri: '/generated-assets/x.webp' },
        metadata_json: null,
        cost_cents: 1,
        duration_ms: 20,
        error: null,
        created_at: '2026-01-01T00:01:01Z',
        completed_at: '2026-01-01T00:01:02Z',
      },
    ]

    const w = mount(RunPlanRenderer, { props: { plan, actionCalls } })

    expect(w.text()).toContain('Demo Run')
    expect(w.text()).toContain('action.execute')
    expect(w.text()).not.toContain('Approvals')
    expect(w.text()).not.toContain('secret')
    expect(w.text()).toContain('[redacted]')
  })

  it('renders approval requests only when the run plan has them', () => {
    const plan: SchemaRunPlanOut = {
      id: 1,
      project_id: 1,
      run_id: 22,
      template_id: null,
      template_version_id: null,
      context_snapshot_id: null,
      key: 'demo.run',
      title: 'Demo Run',
      goal: 'Execute explicit steps.',
      status: RunPlanStatus.started,
      template_key: null,
      template_version: null,
      template_source: null,
      template_origin_path: null,
      template_snapshot_json: null,
      inputs_json: {},
      selected_context_json: null,
      context_filters_json: null,
      grant_snapshot_json: null,
      budget_snapshot_json: null,
      policy_snapshot_json: null,
      output_contract_json: null,
      metadata_json: null,
      created_by: 'agent',
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      started_at: '2026-01-01T00:01:00Z',
      completed_at: null,
      steps: [],
      approval_requests: [
        {
          id: 4,
          project_id: 1,
          run_plan_id: 1,
          run_plan_step_id: null,
          approval_key: 'operator-review',
          title: 'Operator Review',
          description: 'Approve the external spend.',
          status: ApprovalRequestStatus.pending,
          approver: null,
          requested_by: 'agent',
          requested_at: '2026-01-01T00:01:00Z',
          required_when: 'before-step',
          decided_by: null,
          decided_at: null,
          decision_json: null,
          metadata_json: null,
          created_at: '2026-01-01T00:01:00Z',
          updated_at: '2026-01-01T00:01:00Z',
        },
      ],
      consistency_issues: [],
    }

    const w = mount(RunPlanRenderer, { props: { plan } })

    expect(w.text()).toContain('Approvals')
    expect(w.text()).toContain('operator-review')
  })

  it('renders consistency warnings from the backend', () => {
    const plan: SchemaRunPlanOut = {
      id: 1,
      project_id: 1,
      run_id: 22,
      template_id: null,
      template_version_id: null,
      context_snapshot_id: null,
      key: 'demo.run',
      title: 'Demo Run',
      goal: 'Execute explicit steps.',
      status: RunPlanStatus.started,
      template_key: null,
      template_version: null,
      template_source: null,
      template_origin_path: null,
      template_snapshot_json: null,
      inputs_json: {},
      selected_context_json: null,
      context_filters_json: null,
      grant_snapshot_json: null,
      budget_snapshot_json: null,
      policy_snapshot_json: null,
      output_contract_json: null,
      metadata_json: null,
      created_by: 'agent',
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      started_at: '2026-01-01T00:01:00Z',
      completed_at: null,
      steps: [],
      approval_requests: [],
      consistency_issues: [
        {
          code: 'terminal-run-live-plan',
          severity: RunPlanConsistencyIssueOutSeverity.error,
          message: 'Linked audit run is terminal while run plan is still live.',
          run_plan_id: 1,
          run_id: 22,
          step_id: null,
          ticket_key: null,
          data: { run_status: 'aborted' },
        },
      ],
    }

    const w = mount(RunPlanRenderer, { props: { plan } })

    expect(w.text()).toContain('Linked audit run is terminal while run plan is still live.')
    expect(w.text()).toContain('terminal-run-live-plan')
    expect(w.text()).toContain('Run #22')
  })
})
