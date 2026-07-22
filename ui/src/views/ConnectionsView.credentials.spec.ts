import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'

import ConnectionsView from './ConnectionsView.vue'
import {
  authProvider,
  authConnection,
  apiKeyMethod,
  interactiveMethod,
  hasCredentialFields,
  catalogJson,
  clickButton,
  mountConnections,
  json,
} from './connections/connectionsViewTestSupport'

const ORIG_FETCH = globalThis.fetch

describe('ConnectionsView credentials and services', () => {
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
    let connectionLabel = 'Primary Firecrawl'
    const postedBodies: unknown[] = []

    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      if (init?.body) postedBodies.push(JSON.parse(String(init.body)))
      const catalogResponse = catalogJson(url)
      if (catalogResponse) return catalogResponse

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
          ? [
              authConnection({
                revokedAt: revoked ? '2026-05-22T00:02:00Z' : null,
                label: connectionLabel,
              }),
            ]
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
      if (url === '/api/v1/projects/1/auth/credentials/cred_firecrawl' && !init?.method) {
        return json({
          connection: authConnection({ revokedAt: null, label: connectionLabel }),
          values: {},
          secret_present: { api_key: true },
        })
      }
      if (
        url === '/api/v1/projects/1/auth/credentials/cred_firecrawl' &&
        init?.method === 'PATCH'
      ) {
        const body = JSON.parse(String(init.body)) as { label?: string }
        connectionLabel = body.label ?? connectionLabel
        return json({ data: authConnection({ revokedAt: null, label: connectionLabel }) })
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

    const wrapper = mountConnections(router)
    await vi.waitFor(() => expect(wrapper.text()).toContain('No services connected'))
    expect(wrapper.text()).not.toContain('Available services')
    await clickButton(wrapper, 'Add connection')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Firecrawl'))

    const serviceSelect = wrapper.get('button[role="combobox"]')
    expect(serviceSelect.get('img').attributes('src')).toBe(
      '/images/integrations/firecrawl-icon.png',
    )
    await serviceSelect.trigger('click')
    const selectedServiceOption = wrapper.get('[role="option"][aria-selected="true"]')
    expect(selectedServiceOption.get('img').attributes('src')).toBe(
      '/images/integrations/firecrawl-icon.png',
    )
    await selectedServiceOption.trigger('click')

    expect(wrapper.find('[aria-label="Reveal value"]').exists()).toBe(false)
    expect(wrapper.find('[aria-label="Copy value"]').exists()).toBe(false)

    const secretInput = wrapper.find<HTMLInputElement>('input[placeholder="fc-..."]')
    await secretInput.setValue('fc-secret')
    await wrapper.find('input[placeholder="Primary account"]').setValue('Primary')
    await clickButton(wrapper, 'Save and verify')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Primary Firecrawl'))
    await flushPromises()

    expect(wrapper.text()).not.toContain('fc-secret')
    expect(wrapper.html()).not.toContain('fc-secret')
    expect(postedBodies).toContainEqual({
      auth_method_key: 'api_key',
      profile_key: 'default',
      label: 'Primary',
      fields: { api_key: 'fc-secret' },
    })

    await clickButton(wrapper, 'Edit')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Edit connection'))
    expect(wrapper.text()).toContain('Saved — leave blank to keep it.')
    const savedSecretInput = wrapper.find<HTMLInputElement>('input[placeholder="••••••••"]')
    expect(savedSecretInput.exists()).toBe(true)
    expect(savedSecretInput.element.value).toBe('')
    await wrapper.find('input[placeholder="Primary account"]').setValue('Deployment')
    await clickButton(wrapper, 'Save changes')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Deployment'))
    expect(postedBodies).toContainEqual({ label: 'Deployment', fields: {} })

    await clickButton(wrapper, 'Test')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Firecrawl credentials are reachable'))
    expect(postedBodies).toContainEqual({ credential_ref: 'cred_firecrawl' })

    await clickButton(wrapper, 'Revoke')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Revoke this connection?'))
    await clickButton(wrapper, 'Revoke connection')
    await vi.waitFor(() => expect(wrapper.text()).toContain('No services connected'))
    expect(wrapper.findAll('button').some((button) => button.text().trim() === 'Revoke')).toBe(
      false,
    )
    expect(JSON.stringify(postedBodies.filter((body) => !hasCredentialFields(body)))).not.toContain(
      'fc-secret',
    )
  })

  it('stores safe auth method fields with the credential payload', async () => {
    const postedBodies: unknown[] = []
    let connected = false

    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      if (init?.body) postedBodies.push(JSON.parse(String(init.body)))
      const catalogResponse = catalogJson(url)
      if (catalogResponse) return catalogResponse

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
          connections: connected
            ? [
                {
                  ...authConnection({ revokedAt: null }),
                  credential_ref: 'cred_wordpress',
                  provider_key: 'wordpress',
                  auth_type: 'application-password',
                  auth_method_key: 'application_password',
                  label: 'Editorial',
                },
              ]
            : [],
        })
      }
      if (url === '/api/v1/projects/1/auth/wordpress/credentials') {
        connected = true
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

    const wrapper = mountConnections(router)
    await vi.waitFor(() => expect(wrapper.text()).toContain('No services connected'))
    await clickButton(wrapper, 'Add connection')
    await vi.waitFor(() => expect(wrapper.text()).toContain('WordPress'))

    await wrapper.find<HTMLInputElement>('input[placeholder="editor"]').setValue('editor')
    await wrapper.find<HTMLInputElement>('input[placeholder="xxxx xxxx"]').setValue('app pass')
    await wrapper.find('input[placeholder="Primary account"]').setValue('Editorial')
    await wrapper.find('input[placeholder="https://example.com"]').setValue('https://wp.example')
    await clickButton(wrapper, 'Save and verify')

    await vi.waitFor(() => expect(wrapper.text()).toContain('cred_wordpress'))
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

  it('shows required credential errors inline and focuses the first invalid field', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      const catalogResponse = catalogJson(url)
      if (catalogResponse) return catalogResponse

      if (url === '/api/v1/auth/providers') {
        return json([authProvider('firecrawl', 'Firecrawl', 'api-key', apiKeyMethod('fc-...'))])
      }
      if (url === '/api/v1/projects/1/auth/status') {
        return json({ project_id: 1, provider_key: null, providers: [], connections: [] })
      }
      return json({})
    }) as typeof fetch

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/connections', component: ConnectionsView }],
    })
    await router.push('/projects/1/connections')
    await router.isReady()

    const wrapper = mountConnections(router)
    document.body.appendChild(wrapper.element)
    await vi.waitFor(() => expect(wrapper.text()).toContain('No services connected'))
    await clickButton(wrapper, 'Add connection')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Firecrawl'))
    await clickButton(wrapper, 'Save and verify')
    await flushPromises()

    const apiKeyInput = wrapper.get<HTMLInputElement>('#connection-field-api_key')
    expect(wrapper.text()).toContain('API Key is required.')
    expect(apiKeyInput.attributes('aria-invalid')).toBe('true')
    expect(document.activeElement).toBe(apiKeyInput.element)
    wrapper.unmount()
  })

  it('persists an interactive provider draft before starting and navigating to OAuth', async () => {
    const requests: Array<{ url: string; body: Record<string, unknown> }> = []
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)

    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      const catalogResponse = catalogJson(url)
      if (catalogResponse) return catalogResponse

      if (init?.body && url.includes('/auth/')) {
        requests.push({
          url,
          body: JSON.parse(String(init.body)) as Record<string, unknown>,
        })
      }

      if (url === '/api/v1/projects/1/auth/status') {
        return json({ project_id: 1, provider_key: null, providers: [], connections: [] })
      }
      if (url === '/api/v1/auth/providers') {
        return json([authProvider('oauth-demo', 'OAuth Demo', 'oauth2', interactiveMethod())])
      }
      if (url === '/api/v1/projects/1/auth/oauth-demo/credentials') {
        return json(
          {
            data: authConnection({
              revokedAt: null,
              status: 'pending',
              providerKey: 'oauth-demo',
              credentialRef: 'cred_oauth_demo',
              authType: 'oauth2',
              authMethodKey: 'oauth2',
              label: 'Primary OAuth Demo',
            }),
          },
          201,
        )
      }
      if (url === '/api/v1/projects/1/auth/oauth-demo/start') {
        return json({
          data: {
            provider_key: 'oauth-demo',
            auth_method_key: 'oauth2',
            status: 'authorization-pending',
            credential_ref: 'cred_oauth_demo',
            authorization_url: 'https://provider.example/connect?state=safe',
            setup_url: null,
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

    const wrapper = mountConnections(router)
    await vi.waitFor(() => expect(wrapper.text()).toContain('No services connected'))
    await clickButton(wrapper, 'Add connection')
    await vi.waitFor(() => expect(wrapper.text()).toContain('OAuth Demo'))
    await wrapper.get<HTMLInputElement>('#connection-field-client_id').setValue('oauth-client-id')
    await wrapper
      .get<HTMLInputElement>('#connection-field-client_secret')
      .setValue('oauth-client-secret')
    await clickButton(wrapper, 'Connect')

    await vi.waitFor(() => expect(openSpy).toHaveBeenCalledTimes(1))
    expect(requests).toEqual([
      {
        url: '/api/v1/projects/1/auth/oauth-demo/credentials',
        body: {
          auth_method_key: 'oauth2',
          profile_key: 'default',
          label: null,
          fields: {
            client_id: 'oauth-client-id',
            client_secret: 'oauth-client-secret',
          },
        },
      },
      {
        url: '/api/v1/projects/1/auth/oauth-demo/start',
        body: {
          auth_method_key: 'oauth2',
          credential_ref: 'cred_oauth_demo',
        },
      },
    ])
    expect(openSpy).toHaveBeenCalledWith(
      'https://provider.example/connect?state=safe',
      '_self',
      'noopener,noreferrer',
    )
    expect(JSON.stringify(requests)).not.toContain('redirect_uri')
  })

  it.each([
    'javascript:alert(1)',
    'https://provider.example/connect#access_token=fragment-value',
    'https://operator:password@provider.example/connect',
  ])('rejects an unsafe interactive setup URL: %s', async (unsafeUrl) => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)

    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      const catalogResponse = catalogJson(url)
      if (catalogResponse) return catalogResponse

      if (url === '/api/v1/projects/1/auth/status') {
        return json({ project_id: 1, provider_key: null, providers: [], connections: [] })
      }
      if (url === '/api/v1/auth/providers') {
        return json([authProvider('oauth-demo', 'OAuth Demo', 'oauth2', interactiveMethod())])
      }
      if (url === '/api/v1/projects/1/auth/oauth-demo/credentials') {
        return json(
          {
            data: authConnection({
              revokedAt: null,
              status: 'pending',
              providerKey: 'oauth-demo',
              credentialRef: 'cred_oauth_demo',
              authType: 'oauth2',
              authMethodKey: 'oauth2',
            }),
          },
          201,
        )
      }
      if (url === '/api/v1/projects/1/auth/oauth-demo/start') {
        return json({
          data: {
            provider_key: 'oauth-demo',
            auth_method_key: 'oauth2',
            status: 'authorization-pending',
            credential_ref: 'cred_oauth_demo',
            authorization_url: unsafeUrl,
            setup_url: null,
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

    const wrapper = mountConnections(router)
    await vi.waitFor(() => expect(wrapper.text()).toContain('No services connected'))
    await clickButton(wrapper, 'Add connection')
    await vi.waitFor(() => expect(wrapper.text()).toContain('OAuth Demo'))
    await wrapper.get<HTMLInputElement>('#connection-field-client_id').setValue('oauth-client-id')
    await wrapper
      .get<HTMLInputElement>('#connection-field-client_secret')
      .setValue('oauth-client-secret')
    await clickButton(wrapper, 'Connect')

    await vi.waitFor(() =>
      expect(wrapper.text()).toContain('The provider returned an invalid setup URL.'),
    )
    expect(wrapper.html()).not.toContain(unsafeUrl)
    expect(openSpy).not.toHaveBeenCalled()
  })

  it('refreshes connection status and clears callback parameters after OAuth returns', async () => {
    const provider = authProvider('oauth-demo', 'OAuth Demo', 'oauth2', interactiveMethod())
    let connected = false
    let statusRequests = 0

    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      const catalogResponse = catalogJson(url)
      if (catalogResponse) return catalogResponse
      if (url === '/api/v1/projects/1/auth/status') {
        statusRequests += 1
        return json({
          project_id: 1,
          provider_key: null,
          providers: [provider],
          connections: connected
            ? [
                authConnection({
                  revokedAt: null,
                  providerKey: 'oauth-demo',
                  credentialRef: 'cred_oauth_demo',
                  authType: 'oauth2',
                  authMethodKey: 'oauth2',
                  label: 'Primary OAuth Demo',
                }),
              ]
            : [],
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

    const wrapper = mountConnections(router)
    await vi.waitFor(() => expect(wrapper.text()).toContain('No services connected'))
    connected = true
    await router.push('/projects/1/connections?oauth_status=connected&provider_key=oauth-demo')

    await vi.waitFor(() => expect(wrapper.text()).toContain('OAuth Demo connected successfully.'))
    await vi.waitFor(() => expect(router.currentRoute.value.query.oauth_status).toBeUndefined())
    expect(router.currentRoute.value.query.provider_key).toBeUndefined()
    expect(statusRequests).toBeGreaterThanOrEqual(2)
    expect(wrapper.text()).toContain('Primary OAuth Demo')
  })

  it.each([
    ['denied', 'OAuth Demo authorization was denied. Start setup again when you are ready.'],
    ['expired', 'OAuth Demo authorization expired. Start setup again.'],
    [
      'repair-required',
      'OAuth Demo could not finish authorization. Review the connection settings and try again.',
    ],
  ])('renders the %s OAuth return state without opening setup', async (status, message) => {
    const provider = authProvider('oauth-demo', 'OAuth Demo', 'oauth2', interactiveMethod())
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      const catalogResponse = catalogJson(url)
      if (catalogResponse) return catalogResponse
      if (url === '/api/v1/projects/1/auth/status') {
        return json({
          project_id: 1,
          provider_key: null,
          providers: [provider],
          connections: [],
        })
      }
      return json({})
    }) as typeof fetch

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/connections', component: ConnectionsView }],
    })
    await router.push(`/projects/1/connections?oauth_status=${status}&provider_key=oauth-demo`)
    await router.isReady()

    const wrapper = mountConnections(router)
    await vi.waitFor(() => expect(wrapper.text()).toContain(message))
    await vi.waitFor(() => expect(router.currentRoute.value.query.oauth_status).toBeUndefined())
    expect(router.currentRoute.value.query.provider_key).toBeUndefined()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
  })

  it.each(['disabled-provider', 'unknown-provider'])(
    'does not open the add panel for the %s provider deep link',
    async (providerKey) => {
      globalThis.fetch = vi.fn(async (input) => {
        const url = String(input)
        const catalogResponse = catalogJson(url)
        if (catalogResponse) return catalogResponse

        if (url === '/api/v1/projects/1/auth/status') {
          return json({ project_id: 1, provider_key: null, providers: [], connections: [] })
        }
        if (url === '/api/v1/auth/providers') {
          return json([
            authProvider('firecrawl', 'Firecrawl', 'api-key', apiKeyMethod()),
            {
              ...authProvider('disabled-provider', 'Disabled Provider', 'api-key', apiKeyMethod()),
              plugin_slug: 'disabled-plugin',
            },
          ])
        }
        return json({})
      }) as typeof fetch

      const router = createRouter({
        history: createMemoryHistory(),
        routes: [{ path: '/projects/:id/connections', component: ConnectionsView }],
      })
      await router.push(`/projects/1/connections?provider_key=${providerKey}`)
      await router.isReady()

      const wrapper = mountConnections(router)
      await vi.waitFor(() => expect(wrapper.text()).toContain('No services connected'))

      expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
      expect(wrapper.text()).not.toContain('Disabled Provider')
    },
  )

  it('clears a provider deep link when the add panel closes', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      const catalogResponse = catalogJson(url)
      if (catalogResponse) return catalogResponse
      if (url === '/api/v1/auth/providers') {
        return json([authProvider('firecrawl', 'Firecrawl', 'api-key', apiKeyMethod())])
      }
      if (url === '/api/v1/projects/1/auth/status') {
        return json({ project_id: 1, provider_key: null, providers: [], connections: [] })
      }
      return json({})
    }) as typeof fetch

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/connections', component: ConnectionsView }],
    })
    await router.push('/projects/1/connections?provider_key=firecrawl')
    await router.isReady()

    const wrapper = mountConnections(router)
    await vi.waitFor(() => expect(wrapper.find('[role="dialog"]').exists()).toBe(true))
    await clickButton(wrapper, 'Cancel')

    await vi.waitFor(() => expect(router.currentRoute.value.query.provider_key).toBeUndefined())
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
  })

  it('reloads on project route changes without retaining failed project data', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      const catalogResponse = catalogJson(url)
      if (catalogResponse) return catalogResponse
      if (url === '/api/v1/projects/2/plugins?compact=true') return json({}, 500)
      if (url === '/api/v1/plugins?project_id=2&compact=true') return json({}, 500)
      if (url === '/api/v1/auth/providers') {
        return json([authProvider('firecrawl', 'Firecrawl', 'api-key', apiKeyMethod())])
      }
      if (url === '/api/v1/projects/1/auth/status') {
        return json({
          project_id: 1,
          provider_key: null,
          providers: [],
          connections: [authConnection({ revokedAt: null })],
        })
      }
      if (url === '/api/v1/projects/2/auth/status') return json({}, 500)
      return json({})
    }) as typeof fetch

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/connections', component: ConnectionsView }],
    })
    await router.push('/projects/1/connections')
    await router.isReady()

    const wrapper = mountConnections(router)
    await vi.waitFor(() => expect(wrapper.text()).toContain('Primary Firecrawl'))
    await router.push('/projects/2/connections')

    await vi.waitFor(() =>
      expect(wrapper.text()).toContain('/api/v1/projects/2/auth/status failed with 500'),
    )
    expect(wrapper.text()).not.toContain('Primary Firecrawl')
    expect(wrapper.text()).toContain('No services connected')
    expect(wrapper.text()).not.toContain('services are ready')
  })

  it('keeps readiness and setup conclusions hidden until the initial load completes', async () => {
    let resolvePlugins!: (response: Response) => void
    const pluginsPending = new Promise<Response>((resolve) => {
      resolvePlugins = resolve
    })

    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      if (url === '/api/v1/plugins?project_id=1&compact=true') return pluginsPending
      if (url === '/api/v1/projects/1/auth/status') {
        return json({ project_id: 1, provider_key: null, providers: [], connections: [] })
      }
      if (url === '/api/v1/auth/providers') return json([])
      return json({})
    }) as typeof fetch

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/connections', component: ConnectionsView }],
    })
    await router.push('/projects/1/connections')
    await router.isReady()

    const wrapper = mountConnections(router)
    await flushPromises()

    expect(wrapper.find('[aria-label="Loading connection state"]').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('services are ready')
    expect(wrapper.text()).not.toContain('Needs setup')
    expect(wrapper.text()).not.toContain('Reachable')

    resolvePlugins(
      json([
        {
          id: 1,
          slug: 'utils',
          name: 'Utils',
          version: '0.1.0',
          description: '',
          source: 'builtin',
          manifest_json: {},
          enabled_for_project: true,
          created_at: '2026-05-22T00:00:00Z',
          updated_at: '2026-05-22T00:00:00Z',
        },
      ]),
    )
    await vi.waitFor(() =>
      expect(wrapper.find('[aria-label="Loading connection state"]').exists()).toBe(false),
    )
  })

  it('shows Trackbooth under Affiliation in the searchable service selector', async () => {
    const trackboothMethods = [
      {
        key: 'api-key',
        label: 'Trackbooth API key',
        auth_type: 'api-key',
        description: '',
        interactive: false,
        payload_format: 'json',
        payload_field: null,
        fields: [
          {
            key: 'api_key',
            label: 'API Key',
            type: 'secret',
            secret: true,
            required: true,
            placeholder: '',
          },
          {
            key: 'api_base_url',
            label: 'API URL',
            type: 'text',
            secret: false,
            required: false,
            placeholder: 'https://apis.trackbooth.com',
            description: 'Defaults to production.',
          },
        ],
        config: null,
      },
    ]

    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      const now = '2026-05-22T00:00:00Z'
      if (url === '/api/v1/plugins?project_id=1&compact=true') {
        return json([
          {
            id: 1,
            slug: 'media-buying',
            name: 'Media Buying',
            version: '0.1.0',
            description: '',
            source: 'builtin',
            manifest_json: {},
            enabled_for_project: true,
            created_at: now,
            updated_at: now,
          },
          {
            id: 2,
            slug: 'trackbooth',
            name: 'Trackbooth',
            version: '0.1.0',
            description: '',
            source: 'builtin',
            manifest_json: {},
            enabled_for_project: true,
            created_at: now,
            updated_at: now,
          },
        ])
      }
      const catalogResponse = catalogJson(url)
      if (catalogResponse) return catalogResponse

      if (url === '/api/v1/auth/providers') {
        return json([
          {
            id: 1,
            plugin_id: 1,
            plugin_slug: 'media-buying',
            key: 'meta-ads',
            name: 'Meta Ads',
            description: '',
            auth_type: 'oauth',
            auth_methods: apiKeyMethod(),
            scopes: [],
            config_json: {},
          },
          {
            id: 2,
            plugin_id: 2,
            plugin_slug: 'trackbooth',
            key: 'trackbooth',
            name: 'Trackbooth',
            description: 'Trackbooth Agent API provider.',
            auth_type: 'api-key',
            auth_methods: trackboothMethods,
            scopes: [],
            config_json: { connection_category: 'Affiliation' },
          },
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
      return json({})
    }) as typeof fetch

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/connections', component: ConnectionsView }],
    })
    await router.push('/projects/1/connections')
    await router.isReady()

    const wrapper = mountConnections(router)
    await vi.waitFor(() => expect(wrapper.text()).toContain('No services connected'))
    await vi.waitFor(() => expect(wrapper.text()).not.toContain('Loading connections...'))
    await clickButton(wrapper, 'Add connection')
    await vi.waitFor(() => expect(wrapper.find('[role="combobox"]').exists()).toBe(true))

    await wrapper.get('[role="combobox"]').trigger('click')
    await wrapper.get<HTMLInputElement>('input[aria-label="Search options"]').setValue('track')

    const trackboothOption = wrapper.get('[role="option"]')
    expect(trackboothOption.text()).toContain('Trackbooth')
    expect(trackboothOption.text()).toContain('API key')
    expect(wrapper.text()).toContain('Affiliation')

    await wrapper.get('[role="option"]').trigger('click')
    expect(wrapper.text()).toContain('API Key')
    expect(wrapper.find('input[placeholder="https://apis.trackbooth.com"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('does not report failed credentials as connected and keeps operator actions available', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      const catalogResponse = catalogJson(url)
      if (catalogResponse) return catalogResponse

      if (url === '/api/v1/auth/providers') {
        return json([authProvider('firecrawl', 'Firecrawl', 'api-key', apiKeyMethod('fc-...'))])
      }
      if (url === '/api/v1/projects/1/auth/status') {
        return json({
          project_id: 1,
          provider_key: null,
          providers: [],
          connections: [authConnection({ revokedAt: null, status: 'failed' })],
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

    const wrapper = mountConnections(router)
    await vi.waitFor(() => expect(wrapper.text()).toContain('Firecrawl'))

    expect(wrapper.text()).toContain('Failed')
    expect(wrapper.text()).not.toContain('1 connected')
    expect(wrapper.findAll('button').map((button) => button.text().trim())).toContain('Test')
    expect(wrapper.findAll('button').map((button) => button.text().trim())).toContain('Revoke')
  })
})
