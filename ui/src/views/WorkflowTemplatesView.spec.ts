import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'

import {
  WorkflowAgentRequirementSpecRequirement,
  WorkflowSkillRequirementSpecRequirement,
  type SchemaLoadedWorkflowTemplate,
} from '@/api'
import WorkflowTemplatesView from './WorkflowTemplatesView.vue'

const ORIG_FETCH = globalThis.fetch
const EXPECTED_WORKFLOW_STEPS = [
  { id: 'scope-work', title: 'Scope Work' },
  { id: 'define-requirements', title: 'Define Requirements And Flows' },
  { id: 'discover-impact', title: 'Discover Impact' },
  { id: 'plan-tickets', title: 'Plan Tracker Tickets' },
  { id: 'design-approach', title: 'Design Approach' },
  { id: 'review-design', title: 'Review Design' },
  { id: 'design-tests', title: 'Design Tests And Verification' },
  { id: 'deliver-tickets', title: 'Deliver Tickets' },
  { id: 'verify-delivery', title: 'Verify Delivery' },
  { id: 'review-delivery', title: 'Review Delivery' },
  { id: 'audit-tracker', title: 'Audit Tracker Truth' },
  { id: 'release-closeout', title: 'Release Closeout' },
]

describe('WorkflowTemplatesView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)

      if (url === '/api/v1/projects?limit=50') {
        return json({
          items: [
            {
              id: 1,
              slug: 'stackos-local',
              name: 'StackOS Local',
              domain: 'local.stackos',
              locale: 'en-US',
              status: 'active',
              is_active: true,
              settings_json: {},
              created_at: '2026-05-27T00:00:00Z',
              updated_at: '2026-05-27T00:00:00Z',
            },
          ],
          next_cursor: null,
          total_estimate: 1,
        })
      }

      if (url === '/api/v1/projects/1/workflow-templates?plugin_slug=engineering') {
        const loaded = engineeringWorkflow()
        return json({ templates: [loaded.summary], include_shadowed: false })
      }

      if (
        url ===
        '/api/v1/projects/1/workflow-templates/engineering.tracked-delivery?plugin_slug=engineering'
      ) {
        return json(engineeringWorkflow())
      }

      return json({})
    }) as typeof fetch
  })

  afterEach(() => {
    globalThis.fetch = ORIG_FETCH
    document.body.innerHTML = ''
    vi.restoreAllMocks()
  })

  it('renders the Engineering tracked-delivery workflow with the curated SDLC agent subset and skill guidance', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/workflow-templates', component: WorkflowTemplatesView }],
    })
    await router.push('/projects/1/workflow-templates?plugin_slug=engineering')
    await router.isReady()

    const wrapper = mount({ template: '<RouterView />' }, { global: { plugins: [router] } })

    await vi.waitFor(() => expect(wrapper.text()).toContain('Engineering Tracked Delivery'))

    expect(wrapper.text()).toContain('engineering.tracked-delivery')
    await wrapper
      .findAll('tr')
      .find((row) => row.text().includes('Engineering Tracked Delivery'))
      ?.trigger('click')
    await vi.waitFor(() =>
      expect(document.body.textContent ?? '').toContain('Project Setup'),
    )
    const text = document.body.textContent ?? ''
    expect(text).toContain('communication_route_ref')
    expect(text).toContain('support-triage')
    expect(text).toContain('8 agents')
    expect(text).toContain('1 skills')
    expect(text).toContain('stackos.sdlc.requirements-flow-definer')
    expect(text).toContain('stackos.sdlc.codebase-explorer')
    expect(text).toContain('stackos.sdlc.planning')
    expect(text).toContain('stackos.sdlc.architecture')
    expect(text).toContain('stackos.sdlc.test-designer')
    expect(text).toContain('stackos.sdlc.delivery')
    expect(text).toContain('stackos.sdlc.delivery-reviewer')
    expect(text).toContain('stackos.sdlc.release-ops')
    expect(text).toContain('stackos:stackos')
    expect(text).toContain('12 steps')
    for (const step of EXPECTED_WORKFLOW_STEPS) {
      expect(text).toContain(step.id)
      expect(text).toContain(step.title)
    }
    expect(globalThis.fetch).toHaveBeenCalledWith(
      '/api/v1/projects/1/workflow-templates?plugin_slug=engineering',
      expect.anything(),
    )
  })
})

function engineeringWorkflow(): SchemaLoadedWorkflowTemplate {
  return {
    summary: {
      key: 'engineering.tracked-delivery',
      name: 'Engineering Tracked Delivery',
      version: '0.2.0',
      description:
        'Reusable SDLC workflow for requirements, discovery, design, test planning, delivery, verification, review, and release through StackOS tracker state.',
      domain: 'engineering',
      source: 'plugin',
      precedence: 10,
      plugin_slug: 'engineering',
      project_id: null,
      origin_path: 'plugins/engineering/workflows/tracked-delivery.yaml',
      template_id: null,
      version_id: null,
      shadowed_by: null,
      project_extension_id: 9,
      project_extension_enabled: true,
    },
    project_extension: {
      id: 9,
      project_id: 1,
      workflow_key: 'engineering.tracked-delivery',
      enabled: true,
      input_defaults_json: {
        communication_route_ref: 'communication-route:support-feedback',
        canonical_slack_target_ref: 'communication-target:support-triage',
      },
      selected_context_json: {
        communication: {
          target_ref: 'communication-target:support-triage',
        },
      },
      required_input_keys_json: ['communication_route_ref'],
      guardrails_json: { copy_customer_private_data: false },
      step_overrides_json: {
        'scope-work': {
          extra_instructions: ['Use the configured project context.'],
        },
      },
      metadata_json: { owner: 'support' },
      created_by: 'unit-test',
      created_at: '2026-05-27T00:00:00Z',
      updated_at: '2026-05-27T00:00:00Z',
    },
    spec: {
      schema_version: 'stackos.workflow-template.v1',
      key: 'engineering.tracked-delivery',
      name: 'Engineering Tracked Delivery',
      version: '0.2.0',
      description:
        'Reusable SDLC workflow for requirements, discovery, design, test planning, delivery, verification, review, and release through StackOS tracker state.',
      domain: 'engineering',
      inputs: [{ key: 'goal', name: 'Goal', type: 'string', required: true, description: '' }],
      outputs: [
        {
          key: 'delivery_summary',
          name: 'Delivery Summary',
          type: 'object',
          required: true,
          description: '',
        },
      ],
      context_requirements: [],
      agent_requirements: [
        agent(
          'requirements-flow-definer',
          'required',
          'stackos.sdlc.requirements-flow-definer',
        ),
        agent('codebase-explorer', 'recommended', 'stackos.sdlc.codebase-explorer'),
        agent('planning', 'required', 'stackos.sdlc.planning'),
        agent('architecture', 'required', 'stackos.sdlc.architecture'),
        agent('test-designer', 'required', 'stackos.sdlc.test-designer'),
        agent('delivery', 'required', 'stackos.sdlc.delivery'),
        agent('delivery-reviewer', 'required', 'stackos.sdlc.delivery-reviewer'),
        agent('release-ops', 'recommended', 'stackos.sdlc.release-ops'),
      ],
      skill_requirements: [
        {
          skill_ref: 'stackos:stackos',
          requirement: WorkflowSkillRequirementSpecRequirement.recommended,
          purpose: 'Teach the main agent how to operate StackOS MCP and tracker state.',
          applies_to_steps: [],
          setup_notes: ['Adapt generic SDLC presets to the project before creating local agents.'],
        },
      ],
      steps: EXPECTED_WORKFLOW_STEPS.map((step) => ({
        ...step,
        purpose: `${step.title} purpose.`,
      })),
    },
  } as unknown as SchemaLoadedWorkflowTemplate
}

function agent(
  role: string,
  requirement: 'required' | 'recommended',
  agent_preset_ref: string,
) {
  return {
    role,
    requirement:
      requirement === 'required'
        ? WorkflowAgentRequirementSpecRequirement.required
        : WorkflowAgentRequirementSpecRequirement.recommended,
    agent_preset_ref,
    purpose: `${role} purpose`,
    applies_to_steps: [],
    handoff_notes: [],
  }
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  })
}
