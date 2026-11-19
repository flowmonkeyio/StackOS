import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import HomeAgentHostStatus from './HomeAgentHostStatus.vue'
import { useHomeSubscriptions } from './useHomeSubscriptions'

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
    state: { kind: 'loaded', items: ['codex', 'claude-code', 'grok', 'gemini-cli'].map(host_key => ({ host_key, status_label: 'Connected', ok: true })) },
    summary: 'Tools ready', subscriptions: store.subscriptions,
  }, attachTo: document.body })
}

describe('native account picker', () => {
  beforeEach(() => { setActivePinia(createPinia()) })
  afterEach(() => { globalThis.fetch = originalFetch; document.body.innerHTML = ''; vi.useRealTimers() })

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
