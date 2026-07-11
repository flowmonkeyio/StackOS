import { expect } from 'vitest'
import { mount } from '@vue/test-utils'
import type { createRouter } from 'vue-router'

export function authProvider(
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

export function authConnection({
  revokedAt,
  status,
  providerKey = 'firecrawl',
  credentialRef = 'cred_firecrawl',
  authType = 'api-key',
  authMethodKey = 'api_key',
  profileKey = 'default',
  label = 'Primary Firecrawl',
  account = null,
}: {
  revokedAt: string | null
  status?: string
  providerKey?: string
  credentialRef?: string
  authType?: string
  authMethodKey?: string
  profileKey?: string
  label?: string
  account?: Record<string, unknown> | null
}) {
  return {
    credential_ref: credentialRef,
    project_id: 1,
    provider_key: providerKey,
    auth_type: authType,
    auth_method_key: authMethodKey,
    profile_key: profileKey,
    label,
    status: status ?? (revokedAt ? 'revoked' : 'connected'),
    expires_at: null,
    last_tested_at: null,
    revoked_at: revokedAt,
    scopes: [],
    account,
    setup_required: revokedAt !== null || status === 'failed',
  }
}

export function apiKeyMethod(placeholder = 'sk-...') {
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

export function interactiveMethod() {
  return [
    {
      key: 'oauth2',
      label: 'OAuth 2.0',
      auth_type: 'oauth2',
      description: '',
      interactive: true,
      payload_format: 'none',
      payload_field: null,
      fields: [],
      config: null,
    },
  ]
}

export function telegramBotMethod() {
  return [
    {
      key: 'bot-token',
      label: 'Bot token',
      auth_type: 'bot-token',
      description: '',
      interactive: false,
      payload_format: 'json',
      payload_field: null,
      fields: [
        {
          key: 'bot_token',
          label: 'Bot Token',
          type: 'secret',
          secret: true,
          required: true,
          placeholder: '123456:ABC...',
        },
        {
          key: 'webhook_secret_token',
          label: 'Webhook Secret Token',
          type: 'secret',
          secret: true,
          required: false,
          placeholder: '',
        },
        {
          key: 'api_base_url',
          label: 'Local Bot API URL',
          type: 'text',
          secret: false,
          required: false,
          placeholder: 'http://127.0.0.1:8081',
          description:
            "Leave blank for Telegram's hosted Bot API. Use only with the official self-hosted Telegram Bot API server.",
        },
      ],
      config: null,
    },
  ]
}

export function slackBotMethod() {
  return [
    {
      key: 'bot-token',
      label: 'Bot token and signing secret',
      auth_type: 'bot-token',
      description: '',
      interactive: false,
      payload_format: 'json',
      payload_field: null,
      fields: [
        {
          key: 'bot_token',
          label: 'Bot Token',
          type: 'secret',
          secret: true,
          required: true,
          placeholder: 'xoxb-...',
        },
        {
          key: 'signing_secret',
          label: 'Signing Secret',
          type: 'secret',
          secret: true,
          required: true,
          placeholder: '',
          description: 'Used by daemon-side Slack Events API and interaction signature verification.',
        },
      ],
      config: null,
    },
  ]
}

export function hasCredentialFields(body: unknown): boolean {
  return typeof body === 'object' && body !== null && 'fields' in body
}

export function catalogJson(url: string): Response | null {
  const now = '2026-05-22T00:00:00Z'
  if (
    url === '/api/v1/plugins?project_id=1' ||
    url === '/api/v1/plugins?project_id=1&compact=true'
  ) {
    return json([
      {
        id: 1,
        slug: 'utils',
        name: 'Utils',
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
  if (url === '/api/v1/catalog?project_id=1') {
    throw new Error('unexpected aggregate catalog request')
  }
  if (
    [
      '/api/v1/capabilities?project_id=1',
      '/api/v1/providers?project_id=1',
      '/api/v1/actions?project_id=1',
      '/api/v1/resources?project_id=1',
    ].includes(url)
  ) {
    return json([])
  }
  return null
}

export async function clickButton(
  wrapper: ReturnType<typeof mount>,
  label: string,
): Promise<void> {
  const button = wrapper.findAll('button').find((candidate) => candidate.text().trim() === label)
  expect(button, `${label} button`).toBeDefined()
  await button?.trigger('click')
}

export function mountConnections(router: ReturnType<typeof createRouter>): ReturnType<typeof mount> {
  return mount(
    { template: '<RouterView />' },
    {
      global: {
        plugins: [router],
        stubs: { teleport: true },
      },
    },
  )
}

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}
