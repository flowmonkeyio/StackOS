import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'

import SetupStatusTab from './SetupStatusTab.vue'

const ORIG_FETCH = globalThis.fetch

describe('SetupStatusTab', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  afterEach(() => {
    globalThis.fetch = ORIG_FETCH
    vi.restoreAllMocks()
  })

  it('renders runtime and project setup state from generic StackOS endpoints', async () => {
    const requestedUrls: string[] = []
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      requestedUrls.push(url)

      if (url === '/api/v1/health') {
        return json({
          daemon_uptime_s: 14,
          db_status: 'ok',
          scheduler_running: true,
          version: '1.0.0',
          milestone: 'M10',
        })
      }
      if (url === '/api/v1/plugins?project_id=1') {
        return json([plugin('seo', true), plugin('media-buying', false)])
      }
      if (url === '/api/v1/auth/providers') {
        return json([{ key: 'firecrawl', name: 'Firecrawl', plugin_slug: 'utils' }])
      }
      if (url === '/api/v1/projects/1/auth/status') {
        return json({
          project_id: 1,
          provider_key: null,
          providers: [],
          connections: [
            {
              credential_ref: 'cred_firecrawl',
              provider_key: 'firecrawl',
              auth_type: 'api-key',
              auth_method_key: 'api_key',
              profile_key: 'default',
              label: 'Firecrawl',
              status: 'connected',
              scopes: [],
              safe_metadata_json: {},
              expires_at: null,
              revoked_at: null,
              created_at: '2026-05-22T00:00:00Z',
              updated_at: '2026-05-22T00:00:00Z',
            },
          ],
        })
      }
      if (url === '/api/v1/projects/1/workflow-templates') {
        return json({
          templates: [{ key: 'seo.weekly', plugin_slug: 'seo' }],
          include_shadowed: false,
        })
      }
      if (url === '/api/v1/projects/1/workflow-templates/seo.weekly?plugin_slug=seo') {
        return json({
          summary: { key: 'seo.weekly', name: 'SEO Weekly', version: '0.1.0' },
          spec: {
            schema_version: 'stackos.workflow-template.v1',
            key: 'seo.weekly',
            name: 'SEO Weekly',
            version: '0.1.0',
            description: '',
            agent_requirements: [
              {
                role: 'seo-keyword-research',
                requirement: 'required',
                agent_preset_ref: 'seo.workflow.keyword-research',
                purpose: 'Research search demand.',
              },
            ],
            skill_requirements: [
              {
                skill_ref: 'stackos:stackos',
                requirement: 'recommended',
                purpose: 'Use StackOS tracker and run-plan conventions.',
              },
            ],
            steps: [{ id: 'research', title: 'Research' }],
          },
        })
      }
      if (url === '/api/v1/operations?surface=mcp') {
        return json({
          items: [
            operation('workspace.updateProfile'),
            operation('workflowTemplate.describe'),
            operation('resource.query'),
          ],
          groups: [],
        })
      }
      if (url === '/api/v1/operations/action.list/call') {
        return json({
          items: [],
          count: 5,
          hidden_count: 2,
          filters: { project_id: 1 },
        })
      }
      if (url === '/api/v1/operations/integration.list/call') {
        return json({
          project_id: 1,
          items: [],
          count: 3,
          connected_count: 1,
          ready_count: 1,
          exposed_action_count: 5,
          executable_action_count: 4,
          hidden_action_count: 2,
          filters: { project_id: 1 },
        })
      }
      if (url === '/api/v1/operations/agentPreset.list/call') {
        return json({
          presets: [
            {
              key: 'seo.workflow.keyword-research',
              name: 'SEO Keyword Research Agent',
              domain: 'seo',
              role: 'seo-keyword-research',
              plugin_slug: 'seo',
              adaptation_required: true,
              applies_to_workflows: ['seo.weekly'],
            },
          ],
          include_shadowed: false,
        })
      }
      if (url === '/api/v1/operations/workspace.listBindings/call') {
        return json({
          items: [
            {
              id: 9,
              project_id: 1,
              repo_fingerprint: 'path:abc',
              normalized_repo_name: 'content-stack',
              last_known_root: '/Users/example/content-stack',
              framework: null,
              content_model_json: null,
            },
          ],
        })
      }
      if (url === '/api/v1/projects/1/runs?kind=run-plan&limit=1') {
        return json({ items: [], next_cursor: null, total_estimate: 0 })
      }
      return json({})
    }) as typeof fetch

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/setup', component: SetupStatusTab }],
    })
    await router.push('/projects/1/setup')
    await router.isReady()

    const wrapper = mount(SetupStatusTab, { global: { plugins: [router] } })

    await vi.waitFor(() => expect(wrapper.text()).toContain('1 of 2'))
    expect(wrapper.text()).toContain('Setup status')
    expect(wrapper.text()).toContain('Daemon')
    expect(wrapper.text()).toContain('Database')
    expect(wrapper.text()).toContain('Enabled plugins')
    expect(wrapper.text()).toContain('Connections')
    expect(wrapper.text()).toContain('1 connected')
    expect(wrapper.text()).toContain('Workflow templates')
    expect(wrapper.text()).toContain('Agent presets')
    expect(wrapper.text()).toContain('1 generic presets')
    expect(wrapper.text()).toContain('Workspace profile')
    expect(wrapper.text()).toContain('Missing framework, content model')
    expect(wrapper.text()).toContain('Adaptation hints')
    expect(wrapper.text()).toContain('Skills')
    expect(wrapper.text()).toContain('stackos:stackos')
    expect(wrapper.text()).toContain('Operation contracts')
    expect(wrapper.text()).toContain('3 registered')
    expect(wrapper.text()).toContain('Integrations')
    expect(wrapper.text()).toContain('1 connected, 2 actions hidden')
    expect(wrapper.text()).toContain('Available actions')
    expect(wrapper.text()).toContain('5 visible, 2 hidden until setup')
    expect(wrapper.text()).not.toContain('cred_firecrawl')
    expect(wrapper.find('button[aria-label="Open Enabled plugins"]').exists()).toBe(true)
    expect(wrapper.find('button[aria-label="Open Workflow templates"]').exists()).toBe(true)
    expect(wrapper.find('button[aria-label="Open Agent presets"]').exists()).toBe(true)
    expect(wrapper.find('button[aria-label="Open workspace profile operation"]').exists()).toBe(
      true,
    )
    expect(requestedUrls).toContain('/api/v1/health')
    expect(requestedUrls).toContain('/api/v1/projects/1/auth/status')
    expect(requestedUrls).toContain('/api/v1/operations?surface=mcp')
    expect(requestedUrls).toContain('/api/v1/operations/action.list/call')
    expect(requestedUrls).toContain('/api/v1/operations/integration.list/call')
    expect(requestedUrls).toContain('/api/v1/operations/workspace.listBindings/call')
  })
})

function plugin(slug: string, enabled: boolean) {
  return {
    id: slug === 'seo' ? 1 : 2,
    slug,
    name: slug,
    version: '0.1.0',
    description: '',
    source: 'builtin',
    enabled_for_project: enabled,
    manifest_json: {},
    created_at: '2026-05-22T00:00:00Z',
    updated_at: '2026-05-22T00:00:00Z',
  }
}

function operation(name: string) {
  return {
    name,
    category: name.split('.')[0],
    summary: `${name} summary`,
    read_only: true,
    mutating: false,
    surfaces: {
      mcp: { enabled: true, command: null, path: null, notes: null },
      rest: { enabled: true, command: null, path: `/api/v1/operations/${name}/call`, notes: null },
      cli: { enabled: true, command: `ops call ${name}`, path: null, notes: null },
    },
    grant_policy: 'direct-read',
    secret_policy: 'no-secret-output',
  }
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  })
}
