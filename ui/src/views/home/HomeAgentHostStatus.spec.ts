import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import HomeAgentHostStatus from './HomeAgentHostStatus.vue'
import { useHomeSubscriptions } from './useHomeSubscriptions'
import { useToastsStore } from '@/stores/toasts'

const originalFetch = globalThis.fetch
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
const a = { account_ref: 'a', email: 'first@example.com', identity_status: 'verified' }
const b = { account_ref: 'b', email: 'second-long-account-email@example.com', identity_status: 'verified' }
function inventory(accounts = [a, b], desired: string | null = 'a') {
  return { hosts: { codex: { accounts, desired: { account_ref: desired, revision: 0 }, pending_enrollment: null } } }
}
function panel() {
  const store = useHomeSubscriptions()
  return mount(HomeAgentHostStatus, { props: {
    state: { kind: 'loaded', items: ['codex', 'claude-code', 'grok', 'gemini-cli'].map(host_key => ({ host_key, status_label: 'Connected', ok: true, available: true, connection_state: 'connected' as const })) },
    summary: 'Tools ready', subscriptions: store.subscriptions,
  }, attachTo: document.body })
}

describe('native account picker', () => {
  beforeEach(() => { setActivePinia(createPinia()) })
  afterEach(() => { useToastsStore().clear(); globalThis.fetch = originalFetch; document.body.innerHTML = ''; vi.useRealTimers() })

  it('waits for host detection and never adopts an uninstalled host', async () => {
    const empty = { accounts: [], desired: { account_ref: null, revision: 0 }, pending_enrollment: null }
    globalThis.fetch = vi.fn(async url => {
      if (String(url).includes('adoptAccount')) return json({ data: a })
      return json({ hosts: { codex: { ...empty }, grok: { ...empty } } })
    }) as typeof fetch
    const store = useHomeSubscriptions()
    const wrapper = mount(HomeAgentHostStatus, { props: {
      state: { kind: 'loading', items: [] }, summary: '', subscriptions: store.subscriptions,
    } })
    try {
      await flushPromises()
      expect(fetch).not.toHaveBeenCalled()
      await wrapper.setProps({ state: { kind: 'loaded', items: [
        { host_key: 'codex', status_label: 'Connected', available: true, connection_state: 'connected' },
        { host_key: 'grok', status_label: 'Not detected', available: false, connection_state: 'unavailable' },
      ] } })
      await flushPromises()
      const calls = vi.mocked(fetch).mock.calls.filter(([url]) => String(url).includes('adoptAccount'))
      expect(calls).toHaveLength(1)
      expect(JSON.parse(String(calls[0]![1]?.body)).arguments.host_key).toBe('codex')
      expect(wrapper.find('[aria-label="Grok Build accounts"]').exists()).toBe(false)
      await wrapper.setProps({ summary: 'Updated summary' })
      await flushPromises()
      expect(fetch).toHaveBeenCalledTimes(2)
      const items = wrapper.props('state').items
      await wrapper.setProps({ state: { kind: 'loading', items } })
      expect(wrapper.find('[aria-label="Codex account: first@example.com"]').exists()).toBe(true)
      await wrapper.setProps({ state: { kind: 'error', items, message: 'Status unavailable' } })
      expect(wrapper.find('[aria-label="Codex account: first@example.com"]').exists()).toBe(true)
      expect(fetch).toHaveBeenCalledTimes(2)
    } finally { wrapper.unmount() }
  })

  it.each([false, true])('handles detection changes during an account read (retired: %s)', async retired => {
    let finishRead!: (response: Response) => void
    let reads = 0
    globalThis.fetch = vi.fn(async url => {
      if (String(url).includes('adoptAccount')) return json({ data: a })
      if (++reads === 1) return new Promise<Response>(resolve => { finishRead = resolve })
      return json({ hosts: {
        ...inventory().hosts,
        grok: { accounts: [], desired: { account_ref: null, revision: 0 }, pending_enrollment: null },
      } })
    }) as typeof fetch
    const store = useHomeSubscriptions()
    const wrapper = mount(HomeAgentHostStatus, { props: {
      state: { kind: 'loaded', items: [
        { host_key: 'codex', available: true, connection_state: 'connected' },
        { host_key: 'grok', available: false, connection_state: 'unavailable' },
      ] }, summary: '', subscriptions: store.subscriptions,
    } })
    try {
      await flushPromises()
      await wrapper.setProps({ state: { kind: 'loaded', items: [
        { host_key: 'codex', available: true, connection_state: 'connected' },
        { host_key: 'grok', available: true, connection_state: 'connected' },
      ] } })
      expect(reads).toBe(1)
      if (retired) wrapper.unmount()
      finishRead(json(inventory()))
      await flushPromises()
      const adoptions = vi.mocked(fetch).mock.calls.filter(([url]) => String(url).includes('adoptAccount'))
      expect(reads).toBe(retired ? 1 : 2)
      expect(adoptions).toHaveLength(retired ? 0 : 1)
      if (!retired) {
        expect(JSON.parse(String(adoptions[0]![1]?.body)).arguments.host_key).toBe('grok')
        expect(wrapper.find('[aria-label="Grok Build account: first@example.com"]').exists()).toBe(true)
      }
    } finally { if (!retired) wrapper.unmount() }
  })

  it('shows switching immediately and confirms before a slow quota refresh finishes', async () => {
    let finishSwitch!: (response: Response) => void
    let finishQuota!: (response: Response) => void
    globalThis.fetch = vi.fn(async url => {
      if (String(url).includes('selectAccount')) return new Promise<Response>(resolve => { finishSwitch = resolve })
      if (String(url).includes('subscriptions')) return new Promise<Response>(resolve => { finishQuota = resolve })
      return json(inventory())
    }) as typeof fetch
    const store = useHomeSubscriptions()
    store.subscriptions.readings.codex = { plan: 'A plan', meters: [] }
    const wrapper = panel()
    try {
      await flushPromises()
      await wrapper.get('[aria-label="Codex account: first@example.com"]').trigger('click')
      await wrapper.findAll('[role="menuitem"]').find(item => item.text().includes(b.email))!.trigger('click')
      const pending = wrapper.get<HTMLButtonElement>('[aria-label="Codex account: first@example.com"]')
      expect(pending.element.disabled).toBe(true)
      expect(pending.attributes('aria-busy')).toBe('true')
      expect(pending.get('[role="status"]').text()).toBe('Switching account…')
      expect(pending.find('.animate-spin').exists()).toBe(true)
      expect(useToastsStore().items).toHaveLength(0)

      finishSwitch(json({ data: { account_ref: 'b', revision: 1 } }))
      await flushPromises()
      const selected = wrapper.get<HTMLButtonElement>('[aria-label="Codex account: second-long-account-email@example.com"]')
      expect(selected.element.disabled).toBe(false)
      expect(selected.attributes('aria-busy')).toBeUndefined()
      expect(store.subscriptions.loading).toBe(true)
      expect(store.subscriptions.readings.codex).toBeNull()
      expect(useToastsStore().items.at(-1)).toMatchObject({
        kind: 'success', title: 'Codex account switched',
        detail: 'Restart existing Codex processes to use this account. StackOS automated processes will restart automatically.',
      })

      finishQuota(json({ detail: 'Quota temporarily unavailable' }, 503))
      await flushPromises()
      await wrapper.setProps({ subscriptions: store.subscriptions })
      expect(wrapper.find('[aria-label="Codex account: second-long-account-email@example.com"]').exists()).toBe(true)
      expect(store.subscriptions.failed).toBe(true)
      expect(wrapper.text()).toContain('Couldn’t refresh subscription quotas.')
      expect(wrapper.get('li[aria-label="Codex"]').find('[role="alert"]').exists()).toBe(false)
    } finally { wrapper.unmount() }
  })

  it('uses the real operation caller, keyboard menu and successful selection even at zero quota', async () => {
    let finishOldRead!: (response: Response) => void
    let quotaReads = 0
    globalThis.fetch = vi.fn(async (url, init) => {
      const args = JSON.parse(String(init?.body)).arguments
      if (String(url).includes('selectAccount')) {
        expect(args).toMatchObject({ host_key: 'codex', account_ref: 'b', expected_revision: 0 })
        return json({ data: { account_ref: 'b', revision: 1 } })
      }
      if (String(url).includes('subscriptions')) {
        if (quotaReads++ === 0) return new Promise<Response>(resolve => { finishOldRead = resolve })
        return json({ checked_at: new Date().toISOString(), readings: { codex: { plan: 'B plan', meters: [] } }, selections: { codex: { account_ref: 'b', revision: 1 } } })
      }
      return json(inventory())
    }) as typeof fetch
    const store = useHomeSubscriptions()
    store.subscriptions.readings.codex = { plan: 'A plan', meters: [{ scope: 'Weekly', remaining_percent: 0, resets_at: null }] }
    const heldRead = store.load(true)
    const wrapper = panel()
    try {
      await flushPromises()
      expect(vi.mocked(fetch).mock.calls.filter(([url]) => String(url).includes('listAccounts'))).toHaveLength(1)
      expect(wrapper.get('li[aria-label="Gemini CLI"]').find('[aria-haspopup="menu"]').exists()).toBe(false)
      const trigger = wrapper.get('button[aria-label="Codex account: first@example.com"]')
      await trigger.trigger('click')
      expect(wrapper.get('[role="menu"]').text()).toContain('Current')
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown' }))
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }))
      await flushPromises()
      expect(wrapper.get('button[aria-label="Codex account: second-long-account-email@example.com"]').attributes('title')).toBe(b.email)
      expect(wrapper.find('[role="menu"]').exists()).toBe(false)
      expect(store.subscriptions.readings.codex?.plan).toBe('B plan')
      finishOldRead(json({ checked_at: new Date().toISOString(), readings: { codex: { plan: 'Stale A plan', meters: [] } }, selections: { codex: { account_ref: 'a', revision: 0 } } }))
      await heldRead
      expect(store.subscriptions.readings.codex?.plan).toBe('B plan')
      expect(vi.mocked(fetch).mock.calls.filter(([url]) => String(url).includes('hostMcp.status'))).toHaveLength(0)
    } finally { wrapper.unmount() }
  })

  it('retains the old selection and quota when native activation fails', async () => {
    globalThis.fetch = vi.fn(async url => String(url).includes('selectAccount')
      ? json({ detail: 'Native activation failed' }, 400) : json(inventory())) as typeof fetch
    const store = useHomeSubscriptions()
    store.subscriptions.readings.codex = { plan: 'A plan', meters: [] }
    const wrapper = panel()
    try {
      await flushPromises()
      await wrapper.get('[aria-label="Codex account: first@example.com"]').trigger('click')
      await wrapper.findAll('[role="menuitem"]').find(item => item.text().includes(b.email))!.trigger('click')
      await flushPromises()
      expect(wrapper.find('[aria-label="Codex account: first@example.com"]').exists()).toBe(true)
      expect(wrapper.get('[role="alert"]').text()).toContain('Native activation failed')
      expect(store.subscriptions.readings.codex?.plan).toBe('A plan')
      expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes('subscriptions'))).toBe(false)
      expect(useToastsStore().items).toHaveLength(0)
      expect(wrapper.get<HTMLButtonElement>('[aria-label="Codex account: first@example.com"]').element.disabled).toBe(false)
    } finally { wrapper.unmount() }
  })

  it('adopts current identity once and cancels bounded enrollment through the native operation', async () => {
    const enrollment = { enrollment_ref: 'enrollment', status: 'pending', expires_at: new Date(Date.now() + 900_000).toISOString() }
    globalThis.fetch = vi.fn(async url => {
      if (String(url).includes('adoptAccount')) return json({ data: a })
      if (String(url).includes('addAccount')) return json({ data: enrollment })
      if (String(url).includes('cancelAccountEnrollment')) return json({ data: { ...enrollment, status: 'cancelled' } })
      return json(inventory([], null))
    }) as typeof fetch
    const wrapper = panel()
    try {
      await flushPromises()
      expect(wrapper.find('[aria-label="Codex account: first@example.com"]').exists()).toBe(true)
      await wrapper.get('[aria-label="Codex account: first@example.com"]').trigger('click')
      await wrapper.findAll('[role="menuitem"]').find(item => item.text() === 'Add account')!.trigger('click')
      await flushPromises()
      expect(document.body.textContent).toContain('Complete sign-in in the native browser')
      const cancel = [...document.querySelectorAll('button')].find(button => button.textContent?.trim() === 'Cancel')!
      cancel.click(); await flushPromises()
      expect(document.querySelector('[role="dialog"]')).toBeNull()
      expect(vi.mocked(fetch).mock.calls.filter(([url]) => String(url).includes('adoptAccount'))).toHaveLength(1)
      expect(vi.mocked(fetch).mock.calls.filter(([url]) => String(url).includes('cancelAccountEnrollment'))).toHaveLength(1)
    } finally { wrapper.unmount() }
  })

  it('allows first sign-in without a native identity and polls only while enrollment is pending', async () => {
    vi.useFakeTimers()
    let enrolled = false
    const enrollment = { enrollment_ref: 'enrollment', status: 'pending', expires_at: new Date(Date.now() + 900_000).toISOString() }
    globalThis.fetch = vi.fn(async url => {
      if (String(url).includes('adoptAccount')) return json({ detail: 'Identity unverified', data: { code: 'identity_unverified', identity_status: 'needs_sign_in' } }, 400)
      if (String(url).includes('addAccount')) return json({ data: enrollment })
      if (String(url).includes('completeAccountEnrollment')) { enrolled = true; return json({ data: { ...enrollment, status: 'enrolled', account_ref: 'a' } }) }
      return json(inventory(enrolled ? [a] : [], null))
    }) as typeof fetch
    const wrapper = panel()
    try {
      await flushPromises()
      expect(wrapper.find('[role="alert"]').exists()).toBe(false)
      await wrapper.get('[aria-label="Codex account: Choose account"]').trigger('click')
      await wrapper.findAll('[role="menuitem"]').find(item => item.text() === 'Add account')!.trigger('click')
      await flushPromises()
      await vi.advanceTimersByTimeAsync(2000); await flushPromises()
      expect(document.body.textContent).toContain('Account added.')
      expect(wrapper.find('[aria-label="Codex account: Choose account"]').exists()).toBe(true)
      await vi.advanceTimersByTimeAsync(10_000); await flushPromises()
      expect(vi.mocked(fetch).mock.calls.filter(([url]) => String(url).includes('completeAccountEnrollment'))).toHaveLength(1)
      expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes('selectAccount'))).toBe(false)
    } finally { wrapper.unmount() }
  })

  it('does not infer native selection from a single saved account', async () => {
    globalThis.fetch = vi.fn(async url => String(url).includes('adoptAccount')
      ? json({ detail: 'Identity unverified', data: { code: 'identity_unverified', identity_status: 'needs_sign_in' } }, 400)
      : json(inventory([a], null))) as typeof fetch
    const wrapper = panel()
    try {
      await flushPromises()
      expect(wrapper.find('[aria-label="Codex account: Choose account"]').exists()).toBe(true)
      await wrapper.get('[aria-label="Codex account: Choose account"]').trigger('click')
      expect(wrapper.get('[role="menu"]').text()).toContain(a.email)
      expect(wrapper.get('[role="menu"]').text()).not.toContain('Current')
    } finally { wrapper.unmount() }
  })
})
