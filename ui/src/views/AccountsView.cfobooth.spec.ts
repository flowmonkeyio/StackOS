import { afterEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'

import AccountsView from './AccountsView.vue'
import { authConnection, authProvider, clickButton, json } from './connections/connectionsViewTestSupport'

const originalFetch = globalThis.fetch
afterEach(() => { globalThis.fetch = originalFetch; vi.restoreAllMocks() })

describe('CFOBooth Account controls (UI-only API boundary)', () => {
  it('requires explicit inputs, masks the key, rechecks replacement, and explains local disconnect', async () => {
    setActivePinia(createPinia())
    const provider = authProvider('cfobooth', 'CFOBooth', 'api_key', [{
      key: 'api_key', label: 'CFOBooth API key', auth_type: 'api_key', interactive: false,
      payload_format: 'json', permission_verification: null,
      fields: [
        { key: 'api_key', label: 'API key', type: 'password', secret: true, required: true },
        { key: 'api_base_url', label: 'API base URL', type: 'text', required: true,
          placeholder: 'https://cfobooth.com', description: 'Editable example only. No remote deployment has been verified.' },
        { key: 'organization_id', label: 'Organization ID', type: 'text', required: true },
      ],
    }])
    const account = { ...authConnection({ providerKey: 'cfobooth', credentialRef: 'cred_cfo',
      revokedAt: null, authMethodKey: 'api_key', authType: 'api_key', label: 'Local CFO' }), project_ids: [] }
    let saved = false
    let checks = 0
    let releaseTest: (() => void) | undefined
    let releaseReplacement: (() => void) | undefined
    const writes: Array<{ url: string; fields?: Record<string, string> }> = []
    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      if (init?.body) writes.push({ url, ...JSON.parse(String(init.body)) })
      if (url === '/api/v1/auth/accounts') return json({ providers: [provider], accounts: saved ? [account] : [] })
      if (url === '/api/v1/projects?limit=50') return json({ items: [], next_cursor: null })
      if (url === '/api/v1/auth/accounts/cfobooth') { saved = true; return json({ data: account }, 201) }
      if (url.endsWith('/cred_cfo/test')) {
        checks += 1
        if (checks === 1) await new Promise<void>((resolve) => { releaseTest = resolve })
        if (checks === 2) await new Promise<void>((resolve) => { releaseReplacement = resolve })
        return json({ data: { credential_ref: 'cred_cfo', provider_key: 'cfobooth',
          ok: checks === 1, status: checks === 1 ? 'ok' : 'failed',
          summary: checks === 1 ? 'CFO identity checked.' : 'The provider rejected authentication.',
          next_action: checks === 1 ? null : 'Replace the API key and test again.', metadata: {} } })
      }
      if (url.endsWith('/cred_cfo/revoke')) { saved = false; return json({ data: {} }) }
      if (url.endsWith('/cred_cfo')) {
        if (init?.method === 'PATCH') return json({ data: account })
        return json({ account, values: { api_base_url: 'http://localhost:3100', organization_id: 'org' },
          secret_present: { api_key: true } })
      }
      return json({})
    }) as typeof fetch
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/accounts', component: AccountsView }] })
    await router.push('/accounts')
    await router.isReady()
    const wrapper = mount({ template: '<RouterView />' }, { global: { plugins: [router], stubs: { teleport: true } } })
    await vi.waitFor(() => expect(wrapper.text()).toContain('No Accounts yet'))
    await clickButton(wrapper, 'Add Account')
    const endpoint = wrapper.get<HTMLInputElement>('input[placeholder="https://cfobooth.com"]')
    expect(endpoint.element.value).toBe('')
    await clickButton(wrapper, 'Save and verify')
    expect(wrapper.text()).toContain('required')
    expect(writes.filter((w) => w.url.startsWith('/api/v1/auth/accounts'))).toEqual([])
    await wrapper.get('input[type="password"]').setValue('ui-private-canary')
    await wrapper.get('input[placeholder="Service - Default"]').setValue('Local CFO')
    await endpoint.setValue('http://localhost:3100')
    await wrapper.get('input[id$="organization_id"]').setValue('org')
    await clickButton(wrapper, 'Save and verify')
    await vi.waitFor(() => expect(releaseTest).toBeTypeOf('function'))
    expect(wrapper.text()).not.toContain('Complete the required fields to continue.')
    expect(wrapper.html()).not.toContain('ui-private-canary')
    releaseTest!()
    await vi.waitFor(() => expect(wrapper.text()).toContain('CFO identity checked.'))
    await clickButton(wrapper, 'Edit')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Authentication method is locked'))
    expect(wrapper.get<HTMLInputElement>('input[type="password"]').element.value).toBe('')
    await wrapper.get('input[type="password"]').setValue('ui-replacement-canary')
    await wrapper.get('input[id$="api_base_url"]').setValue('http://127.0.0.1:3200')
    await clickButton(wrapper, 'Save changes')
    await vi.waitFor(() => expect(releaseReplacement).toBeTypeOf('function'))
    expect(wrapper.text()).toContain('Edit Account')
    expect(wrapper.get<HTMLInputElement>('input[id$="api_base_url"]').element.value).toBe('http://127.0.0.1:3200')
    releaseReplacement!()
    await vi.waitFor(() => expect(wrapper.text()).toContain('Replace the API key and test again.'))
    expect(checks).toBe(2)
    expect(writes.find((w) => w.url.endsWith('/cred_cfo'))?.fields).toEqual({
      api_base_url: 'http://127.0.0.1:3200', organization_id: 'org', api_key: 'ui-replacement-canary',
    })
    expect(wrapper.html()).not.toContain('ui-replacement-canary')
    await clickButton(wrapper, 'Disconnect')
    expect(wrapper.text()).toContain('This does not revoke the remote key')
    expect(wrapper.text()).toContain('API key management in your CFOBooth organization')
    await wrapper.get('[role="dialog"]').findAll('button')
      .find((button) => button.text() === 'Disconnect')!.trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('No Accounts yet'))
    expect(writes.some((w) => w.url.endsWith('/start'))).toBe(false)
  })
})
