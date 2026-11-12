import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import type { SchemaAccountOut } from '@/api'
import HomeConnectionsSection from './HomeConnectionsSection.vue'

function account(id: number, overrides: Partial<SchemaAccountOut> = {}): SchemaAccountOut {
  return { credential_id: id, credential_ref: `credential_${id}`, provider_key: 'telegram',
    display_name: 'Operations', status: 'connected', setup_required: false, auth_type: 'token',
    auth_method_key: 'bot', scopes: [], expires_at: null, revoked_at: null, last_tested_at: null,
    ...overrides }
}

async function render(props: { accounts: SchemaAccountOut[]; loading: boolean; error?: string; projectId?: number }) {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', component: { template: '<div />' } },
    { path: '/accounts', component: { template: '<div>Accounts owner</div>' } },
    { path: '/projects/:id/connections', component: { template: '<div>Project connections owner</div>' } },
  ] })
  await router.push('/')
  const wrapper = mount(HomeConnectionsSection, { props, global: { plugins: [router] } })
  return { wrapper, router }
}

afterEach(() => { vi.useRealTimers() })

describe('HomeConnectionsSection', () => {
  it('retains every account and exposes duplicate identity and full status on keyboard focus', async () => {
    const { wrapper } = await render({ accounts: [account(11), account(12), account(13, { display_name: 'Personal' })], loading: false })
    try {
      expect(wrapper.findAll('li')).toHaveLength(3)
      expect(wrapper.findAll('li').map(row => row.text())).toEqual([
        'TelegramOperations · #11', 'TelegramOperations · #12', 'TelegramPersonal',
      ])
      expect(wrapper.findAll('[role="img"]').map(icon => icon.attributes('aria-label'))).toEqual(['Connected', 'Connected', 'Connected'])
      expect(wrapper.find('[data-kind="connection"]').exists()).toBe(false)
      vi.useFakeTimers()
      await wrapper.get('[tabindex="0"]').trigger('focusin')
      await vi.advanceTimersByTimeAsync(350)
      expect(wrapper.get('[role="tooltip"]').text()).toBe('Telegram · Operations · #11 · Connected')
    } finally { wrapper.unmount() }
  })

  it('preserves failed tests, setup, expired, revoked, disconnected and unknown state truth', async () => {
    const { wrapper } = await render({ accounts: [
      account(1, { last_test: { ok: false } as SchemaAccountOut['last_test'] }),
      account(2, { setup_required: true }), account(3, { status: 'expired' }),
      account(4, { status: 'revoked' }), account(5, { status: 'disconnected' }),
      account(6, { status: 'unavailable' }),
    ], loading: false })
    try {
      expect(wrapper.findAll('[role="img"]').map(icon => icon.attributes('aria-label')))
        .toEqual(['Test failed', 'Setup needed', 'Expired', 'Revoked', 'Disconnected', 'Unavailable'])
      expect(wrapper.get('[aria-label="Test failed"]').classes()).toContain('text-danger-fg')
      expect(wrapper.get('[aria-label="Setup needed"]').classes()).toContain('text-warning-fg')
    } finally { wrapper.unmount() }
  })

  it.each([undefined, 7])('keeps empty/error/loading distinct and routes management in scope %s', async projectId => {
    const { wrapper, router } = await render({ accounts: [], loading: true, projectId })
    try {
      expect(wrapper.text()).not.toContain('No Accounts')
      await wrapper.setProps({ loading: false })
      expect(wrapper.text()).toContain(projectId ? 'No Accounts are attached to this project.' : 'No Accounts are connected.')
      await wrapper.setProps({ error: 'Connections unavailable' })
      expect(wrapper.text()).toContain('Connections unavailable')
      expect(wrapper.text()).not.toContain('No Accounts')
      await wrapper.get('a').trigger('click')
      await flushPromises()
      expect(router.currentRoute.value.fullPath).toBe(projectId ? '/projects/7/connections?section=services' : '/accounts')
    } finally { wrapper.unmount() }
  })
})
