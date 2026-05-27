import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'

import AgentPresetsView from './AgentPresetsView.vue'

const ORIG_FETCH = globalThis.fetch

describe('AgentPresetsView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      const body = init?.body ? JSON.parse(String(init.body)) : {}

      if (url === '/api/v1/projects?limit=50') {
        return json({
          items: [
            {
              id: 1,
              slug: 'operatorbot-tracker',
              name: 'Operatorbot Tracker',
              domain: 'local',
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

      if (url === '/api/v1/operations/agentPreset.list/call') {
        return json({
          presets: [
            {
              key: 'stackos.sdlc.planning',
              name: 'Planning Agent',
              version: '0.1.0',
              description: 'Breaks work into sequenced tickets and dependencies.',
              domain: 'sdlc',
              role: 'planning',
              agent_type: 'planner',
              source: 'builtin',
              precedence: 0,
              plugin_slug: null,
              workflow_roles: [],
              applies_to_workflows: [],
              generic_preset: true,
              adaptation_required: true,
            },
            {
              key: 'seo.workflow.keyword-research',
              name: 'SEO Keyword Research Agent',
              version: '0.1.0',
              description: 'Researches search demand.',
              domain: 'seo',
              role: 'seo-keyword-research',
              agent_type: 'specialist',
              source: 'builtin',
              precedence: 10,
              plugin_slug: 'seo',
              workflow_roles: ['research'],
              applies_to_workflows: ['seo.keyword-research'],
              generic_preset: true,
              adaptation_required: true,
            },
          ],
          include_shadowed: false,
        })
      }

      if (url === '/api/v1/operations/agentPreset.describe/call') {
        const key = body.arguments?.key ?? 'stackos.sdlc.planning'
        return json(describePreset(String(key)))
      }

      return json({})
    }) as typeof fetch
  })

  afterEach(() => {
    globalThis.fetch = ORIG_FETCH
    vi.restoreAllMocks()
  })

  it('defaults to SDLC presets and shows project adaptation guidance', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/agent-presets', component: AgentPresetsView }],
    })
    await router.push('/projects/1/agent-presets')
    await router.isReady()

    const wrapper = mount(AgentPresetsView, { global: { plugins: [router] } })

    await vi.waitFor(() => expect(wrapper.text()).toContain('Planning Agent'))

    expect(wrapper.text()).toContain('Agent Presets')
    expect(wrapper.text()).toContain('SDLC')
    expect(wrapper.text()).toContain('project-specific setup required')
    expect(wrapper.text()).toContain('do not use verbatim')
    expect(wrapper.text()).toContain('Create dependency-aware tickets.')
    expect(wrapper.text()).not.toContain('SEO Keyword Research Agent')
  })
})

function describePreset(key: string) {
  const summary = {
    key,
    name: key === 'stackos.sdlc.planning' ? 'Planning Agent' : 'SEO Keyword Research Agent',
    version: '0.1.0',
    description:
      key === 'stackos.sdlc.planning'
        ? 'Breaks work into sequenced tickets and dependencies.'
        : 'Researches search demand.',
    domain: key === 'stackos.sdlc.planning' ? 'sdlc' : 'seo',
    role: key === 'stackos.sdlc.planning' ? 'planning' : 'seo-keyword-research',
    agent_type: key === 'stackos.sdlc.planning' ? 'planner' : 'specialist',
    source: 'builtin',
    precedence: 0,
    plugin_slug: key === 'stackos.sdlc.planning' ? null : 'seo',
    workflow_roles: key === 'stackos.sdlc.planning' ? [] : ['research'],
    applies_to_workflows: key === 'stackos.sdlc.planning' ? [] : ['seo.keyword-research'],
    generic_preset: true,
    adaptation_required: true,
  }

  return {
    preset: {
      summary,
      preset: {
        ...summary,
        prompt_contract: {
          mission: 'Plan the work through StackOS tracker state.',
          must_do: ['Create dependency-aware tickets.'],
          success_criteria: ['No loose ends remain in the plan.'],
        },
        project_adaptation: {
          required: true,
          do_not_use_verbatim: true,
          instruction: 'Adjust this preset to the project-specific setup.',
        },
        recommended_tools: ['tracker.createTicket'],
      },
    },
    project_adaptation: {
      adaptation_required: true,
      do_not_use_verbatim: true,
      instruction: 'Adjust this preset to the project-specific setup.',
      prompt_assembly_order: ['preset', 'project context'],
      required_agent_action: 'Adapt before use.',
    },
    setup_guidance: ['Review project docs and tracker state before use.'],
  }
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  })
}
