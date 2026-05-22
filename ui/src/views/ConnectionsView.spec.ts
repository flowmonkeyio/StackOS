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
          authProvider('firecrawl', 'Firecrawl', 'api-key'),
          authProvider('local-files', 'Local Files', 'local'),
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

    const secretInput = wrapper.find<HTMLInputElement>('input[placeholder="Paste credential"]')
    await secretInput.setValue('fc-secret')
    await wrapper.find('input[placeholder="Primary"]').setValue('Primary')
    await clickButton(wrapper, 'Save')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Stored cred_firecrawl.'))
    await flushPromises()

    expect(secretInput.element.value).toBe('')
    expect(wrapper.text()).not.toContain('fc-secret')
    expect(wrapper.html()).not.toContain('fc-secret')
    expect(postedBodies).toContainEqual({
      plaintext_payload: 'fc-secret',
      payload_encoding: 'plain',
      config_json: { label: 'Primary' },
    })

    await clickButton(wrapper, 'Test')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Firecrawl credentials are reachable'))
    expect(postedBodies).toContainEqual({ credential_ref: 'cred_firecrawl' })

    await clickButton(wrapper, 'Revoke')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Revoked cred_firecrawl.'))
    expect(JSON.stringify(postedBodies.filter((body) => !hasPlaintextPayload(body)))).not.toContain(
      'fc-secret',
    )
  })

  it('stores safe provider setup fields with the credential payload', async () => {
    const postedBodies: unknown[] = []

    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      if (init?.body) postedBodies.push(JSON.parse(String(init.body)))

      if (url === '/api/v1/auth/providers') {
        return json([
          authProvider('wordpress', 'WordPress', 'api-key', {
            setup_fields: [
              {
                key: 'wp_url',
                label: 'Site URL',
                type: 'url',
                required: true,
                placeholder: 'https://example.com',
              },
            ],
          }),
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

    await wrapper.find<HTMLInputElement>('input[placeholder="Paste credential"]').setValue(
      '{"username":"editor","application_password":"app pass"}',
    )
    await wrapper.find('input[placeholder="Primary"]').setValue('Editorial')
    await wrapper.find('input[placeholder="https://example.com"]').setValue('https://wp.example')
    await clickButton(wrapper, 'Save')

    await vi.waitFor(() => expect(wrapper.text()).toContain('Stored cred_wordpress.'))
    expect(postedBodies).toContainEqual({
      plaintext_payload: '{"username":"editor","application_password":"app pass"}',
      payload_encoding: 'plain',
      config_json: {
        label: 'Editorial',
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
  configJson: Record<string, unknown> | null = null,
) {
  return {
    id: key === 'firecrawl' ? 1 : 2,
    plugin_id: 1,
    plugin_slug: 'utils',
    key,
    name,
    description: '',
    auth_type: authType,
    scopes: [],
    config_json: configJson,
  }
}

function authConnection({ revokedAt }: { revokedAt: string | null }) {
  return {
    credential_ref: 'cred_firecrawl',
    project_id: 1,
    provider_key: 'firecrawl',
    auth_type: 'api-key',
    status: revokedAt ? 'revoked' : 'connected',
    expires_at: null,
    last_tested_at: null,
    revoked_at: revokedAt,
    scopes: [],
    account: null,
    setup_required: revokedAt !== null,
  }
}

function hasPlaintextPayload(body: unknown): boolean {
  return typeof body === 'object' && body !== null && 'plaintext_payload' in body
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
