import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'

import ConnectionsView from './ConnectionsView.vue'

const ORIG_FETCH = globalThis.fetch

describe('ConnectionsView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  afterEach(() => {
    globalThis.fetch = ORIG_FETCH
    vi.restoreAllMocks()
  })

  it('stores, tests, and revokes provider credentials without rendering secrets', async () => {
    let connected = false
    let revoked = false
    const postedBodies: unknown[] = []

    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      if (init?.body) postedBodies.push(JSON.parse(String(init.body)))

      if (url === '/api/v1/auth/providers') {
        return json([
          authProvider('firecrawl', 'Firecrawl', 'api-key', apiKeyMethod('fc-...')),
          authProvider('local-files', 'Local Files', 'local', [
            {
              key: 'local',
              label: 'Local daemon',
              auth_type: 'local',
              description: '',
              interactive: false,
              payload_format: 'none',
              payload_field: null,
              fields: [],
              config: null,
            },
          ]),
        ])
      }
      if (url === '/api/v1/projects/1/auth/status') {
        const connections = connected
          ? [authConnection({ revokedAt: revoked ? '2026-05-22T00:02:00Z' : null })]
          : []
        return json({
          project_id: 1,
          provider_key: null,
          providers: [],
          connections,
        })
      }
      if (url === '/api/v1/projects/1/auth/firecrawl/credentials') {
        connected = true
        return json({ data: authConnection({ revokedAt: null }) }, 201)
      }
      if (url === '/api/v1/projects/1/auth/test') {
        return json({
          data: {
            credential_ref: 'cred_firecrawl',
            provider_key: 'firecrawl',
            ok: true,
            status: 'ok',
            summary: 'Firecrawl credentials are reachable',
            checked_at: '2026-05-22T00:01:00Z',
            retryable: false,
            next_action: null,
            metadata: {},
          },
        })
      }
      if (url === '/api/v1/projects/1/auth/revoke') {
        revoked = true
        return json({
          data: {
            credential_ref: 'cred_firecrawl',
            provider_key: 'firecrawl',
            project_id: 1,
            revoked_at: '2026-05-22T00:02:00Z',
            status: 'revoked',
          },
        })
      }
      return json({})
    }) as typeof fetch

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/connections', component: ConnectionsView }],
    })
    await router.push('/projects/1/connections')
    await router.isReady()

    const wrapper = mount(ConnectionsView, { global: { plugins: [router] } })
    await vi.waitFor(() => expect(wrapper.text()).toContain('Firecrawl'))

    expect(wrapper.text()).toContain('No credential required.')
    expect(wrapper.find('[aria-label="Reveal value"]').exists()).toBe(false)
    expect(wrapper.find('[aria-label="Copy value"]').exists()).toBe(false)

    const secretInput = wrapper.find<HTMLInputElement>('input[placeholder="fc-..."]')
    await secretInput.setValue('fc-secret')
    await wrapper.find('input[placeholder="Primary"]').setValue('Primary')
    await clickButton(wrapper, 'Save')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Stored cred_firecrawl.'))
    await flushPromises()

    expect(secretInput.element.value).toBe('')
    expect(wrapper.text()).not.toContain('fc-secret')
    expect(wrapper.html()).not.toContain('fc-secret')
    expect(postedBodies).toContainEqual({
      auth_method_key: 'api_key',
      profile_key: 'default',
      label: 'Primary',
      fields: { api_key: 'fc-secret' },
    })

    await clickButton(wrapper, 'Test')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Firecrawl credentials are reachable'))
    expect(postedBodies).toContainEqual({ credential_ref: 'cred_firecrawl' })

    await clickButton(wrapper, 'Revoke')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Revoked cred_firecrawl.'))
    expect(JSON.stringify(postedBodies.filter((body) => !hasCredentialFields(body)))).not.toContain(
      'fc-secret',
    )
  })

  it('stores safe auth method fields with the credential payload', async () => {
    const postedBodies: unknown[] = []

    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      if (init?.body) postedBodies.push(JSON.parse(String(init.body)))

      if (url === '/api/v1/auth/providers') {
        return json([
          authProvider('wordpress', 'WordPress', 'application-password', [
            {
              key: 'application_password',
              label: 'Application password',
              auth_type: 'application-password',
              description: '',
              interactive: false,
              payload_format: 'json',
              payload_field: null,
              config: null,
              fields: [
                {
                  key: 'username',
                  label: 'Username',
                  type: 'secret',
                  secret: true,
                  required: true,
                  placeholder: 'editor',
                },
                {
                  key: 'application_password',
                  label: 'Application Password',
                  type: 'secret',
                  secret: true,
                  required: true,
                  placeholder: 'xxxx xxxx',
                },
                {
                  key: 'wp_url',
                  label: 'Site URL',
                  type: 'url',
                  secret: false,
                  required: true,
                  placeholder: 'https://example.com',
                },
              ],
            },
          ]),
        ])
      }
      if (url === '/api/v1/projects/1/auth/status') {
        return json({
          project_id: 1,
          provider_key: null,
          providers: [],
          connections: [],
        })
      }
      if (url === '/api/v1/projects/1/auth/wordpress/credentials') {
        return json(
          {
            data: {
              ...authConnection({ revokedAt: null }),
              credential_ref: 'cred_wordpress',
              provider_key: 'wordpress',
            },
          },
          201,
        )
      }
      return json({})
    }) as typeof fetch

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/connections', component: ConnectionsView }],
    })
    await router.push('/projects/1/connections')
    await router.isReady()

    const wrapper = mount(ConnectionsView, { global: { plugins: [router] } })
    await vi.waitFor(() => expect(wrapper.text()).toContain('WordPress'))

    await wrapper.find<HTMLInputElement>('input[placeholder="editor"]').setValue('editor')
    await wrapper.find<HTMLInputElement>('input[placeholder="xxxx xxxx"]').setValue('app pass')
    await wrapper.find('input[placeholder="Primary"]').setValue('Editorial')
    await wrapper.find('input[placeholder="https://example.com"]').setValue('https://wp.example')
    await clickButton(wrapper, 'Save')

    await vi.waitFor(() => expect(wrapper.text()).toContain('Stored cred_wordpress.'))
    expect(postedBodies).toContainEqual({
      auth_method_key: 'application_password',
      profile_key: 'default',
      label: 'Editorial',
      fields: {
        username: 'editor',
        application_password: 'app pass',
        wp_url: 'https://wp.example',
      },
    })
    expect(wrapper.text()).not.toContain('app pass')
  })
})

function authProvider(
  key: string,
  name: string,
  authType: string,
  authMethods: unknown[] = apiKeyMethod(),
) {
  return {
    id: key === 'firecrawl' ? 1 : 2,
    plugin_id: 1,
    plugin_slug: 'utils',
    key,
    name,
    description: '',
    auth_type: authType,
    auth_methods: authMethods,
    scopes: [],
    config_json: { auth_methods: authMethods },
  }
}

function authConnection({ revokedAt }: { revokedAt: string | null }) {
  return {
    credential_ref: 'cred_firecrawl',
    project_id: 1,
    provider_key: 'firecrawl',
    auth_type: 'api-key',
    auth_method_key: 'api_key',
    profile_key: 'default',
    label: 'Primary Firecrawl',
    status: revokedAt ? 'revoked' : 'connected',
    expires_at: null,
    last_tested_at: null,
    revoked_at: revokedAt,
    scopes: [],
    account: null,
    setup_required: revokedAt !== null,
  }
}

function apiKeyMethod(placeholder = 'sk-...') {
  return [
    {
      key: 'api_key',
      label: 'API key',
      auth_type: 'api-key',
      description: '',
      interactive: false,
      payload_format: 'raw',
      payload_field: 'api_key',
      fields: [
        {
          key: 'api_key',
          label: 'API Key',
          type: 'secret',
          secret: true,
          required: true,
          placeholder,
        },
      ],
      config: null,
    },
  ]
}

function hasCredentialFields(body: unknown): boolean {
  return typeof body === 'object' && body !== null && 'fields' in body
}

async function clickButton(wrapper: ReturnType<typeof mount>, label: string): Promise<void> {
  const button = wrapper
    .findAll('button')
    .find((candidate) => candidate.text().trim() === label)
  expect(button, `${label} button`).toBeDefined()
  await button?.trigger('click')
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}
