import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import {
  WorkflowAgentRequirementSpecRequirement,
  WorkflowSkillPresetRequirementSpecRequirement,
  WorkflowSkillRequirementSpecRequirement,
  type SchemaLoadedWorkflowTemplate,
} from '@/api'
import TemplateRenderer from './TemplateRenderer.vue'

describe('TemplateRenderer', () => {
  it('renders reusable workflow structure and not action payloads', () => {
    const template: SchemaLoadedWorkflowTemplate = {
      summary: {
        key: 'core.review',
        name: 'Review',
        version: '0.1.0',
        description: 'Review context.',
        domain: 'core',
        source: 'plugin',
        precedence: 10,
        plugin_slug: 'core',
        project_id: null,
        origin_path: 'plugins/core/workflows/review.yaml',
        template_id: null,
        version_id: null,
        shadowed_by: null,
        project_extension_id: 7,
        project_extension_enabled: true,
      },
      spec: {
        schema_version: 'stackos.workflow-template.v1',
        key: 'core.review',
        name: 'Review',
        version: '0.1.0',
        description: 'Review context.',
        domain: 'core',
        inputs: [{ key: 'goal', name: 'Goal', type: 'string', required: true, description: '' }],
        outputs: [{ key: 'plan', name: 'Plan', type: 'object', required: true, description: '' }],
        context_requirements: [
          {
            id: 'recent_runs',
            source: 'runs',
            purpose: 'Read bounded run context.',
            fields: ['kind', 'status'],
            max_items: 10,
            return_mode: 'compact',
          },
        ],
        agent_requirements: [
          {
            role: 'planning',
            requirement: WorkflowAgentRequirementSpecRequirement.required,
            agent_preset_ref: 'stackos.sdlc.planning',
            purpose: 'Plan tickets and dependencies.',
            applies_to_steps: ['review'],
            handoff_notes: ['Use tracker tickets for sequencing.'],
          },
        ],
        skill_requirements: [
          {
            skill_ref: 'stackos:stackos',
            requirement: WorkflowSkillRequirementSpecRequirement.recommended,
            purpose: 'Use StackOS MCP, tracker, run plans, and evidence conventions.',
            applies_to_steps: ['review'],
            setup_notes: ['Load the StackOS skill before workflow execution when supported.'],
          },
        ],
        skill_preset_requirements: [
          {
            skill_preset_ref: 'stackos.sdlc.delivery-orchestrator',
            requirement: WorkflowSkillPresetRequirementSpecRequirement.required,
            purpose: 'Coordinate the delivery lifecycle from workflow state.',
            applies_to_steps: ['review'],
            setup_notes: ['Adapt this workflow-specific skill preset before execution.'],
          },
        ],
        steps: [
          {
            id: 'review',
            title: 'Review',
            purpose: 'Review bounded context.',
            context_refs: ['recent_runs'],
            output_refs: ['plan'],
          },
        ],
      },
    }
    ;(
      template as SchemaLoadedWorkflowTemplate & {
        project_extension: {
          id: number
          workflow_key: string
          enabled: boolean
          input_defaults_json: Record<string, unknown>
          selected_context_json: Record<string, unknown>
          required_input_keys_json: string[]
          guardrails_json: Record<string, unknown>
          step_overrides_json: Record<string, unknown>
          template_overrides_json: Record<string, unknown>
          metadata_json: Record<string, unknown>
        }
      }
    ).project_extension = {
      id: 7,
      project_id: 1,
      workflow_key: 'core.review',
      enabled: true,
      input_defaults_json: { communication_route_ref: 'communication-route:support' },
      selected_context_json: { communication: { target_ref: 'communication-target:support' } },
      required_input_keys_json: ['communication_route_ref'],
      guardrails_json: { copy_customer_private_data: false },
      step_overrides_json: { review: { extra_instructions: ['Use configured route.'] } },
      template_overrides_json: { description: 'Project support review flow.' },
      metadata_json: { owner: 'support' },
      created_at: '2026-05-27T00:00:00Z',
      updated_at: '2026-05-27T00:00:00Z',
    }

    const w = mount(TemplateRenderer, { props: { template } })

    expect(w.text()).toContain('Review')
    expect(w.text()).toContain('Workflow journey')
    expect(w.text()).toContain('Project readiness')
    expect(w.text()).toContain('Ready for this project')
    expect(w.text()).toContain('StackOS keeps the plan')
    expect(w.text()).toContain('it does not do the work itself')
    expect(w.text()).toContain('communication_route_ref')
    expect(w.text()).toContain('Workflow changes')
    expect(w.text()).toContain('review')
    expect(w.text()).toContain('recent_runs')
    expect(w.text()).toContain('Who needs to be involved')
    expect(w.text()).toContain('stackos.sdlc.planning')
    expect(w.text()).toContain('stackos:stackos')
    expect(w.text()).toContain('stackos.sdlc.delivery-orchestrator')
    expect(w.text()).toContain('Adapt this workflow-specific skill preset before execution.')
    expect(w.text()).toContain('What the user gets')
    expect(w.text()).toContain('Technical contract')
    expect(w.text()).not.toContain('credential_ref')
  })
})
