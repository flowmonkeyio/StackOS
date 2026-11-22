import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import HomeAgentHostStatus from './HomeAgentHostStatus.vue'
import { useHomeSubscriptions } from './useHomeSubscriptions'
import { useToastsStore } from '@/stores/toasts'
import { UiScoreMeter } from '@/components/ui'

const originalFetch = globalThis.fetch
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
const a = { account_ref: 'a', email: 'first@example.com', identity_status: 'verified' }
const b = { account_ref: 'b', email: 'second-long-account-email@example.com', identity_status: 'verified' }
function inventory(accounts = [a, b], desired: string | null = 'a') {
  return { hosts: { codex: { accounts, desired: { account_ref: desired, revision: 0 }, pending_enrollment: null } } }
}
function panel() {
  const store = useHomeSubscriptions()
  store.subscriptions.loading = false
  return mount(HomeAgentHostStatus, { props: {
    state: { kind: 'loaded', items: ['codex', 'claude-code', 'grok', 'antigravity'].map(host_key => ({ host_key, status_label: 'Connected', ok: true, available: true, connection_state: 'connected' as const })) },
    summary: 'Tools ready', subscriptions: store.subscriptions,
  }, attachTo: document.body })
}
const hostKeys = ['codex', 'claude-code', 'grok', 'claude-desktop', 'antigravity', 'hermes']
function detection(available: string[]) {
  return { kind: 'loaded' as const, items: hostKeys.map(host_key => ({ host_key,
    available: available.includes(host_key), connection_state: available.includes(host_key) ? 'connected' as const : 'unavailable' as const,
    status_label: available.includes(host_key) ? 'Connected' : 'Not detected',
  })) }
}
function emptyInventory() {
  return { hosts: Object.fromEntries(['codex', 'claude-code', 'grok'].map(host => [host,
    { accounts: [], desired: { account_ref: null, revision: 0 }, pending_enrollment: null }])) }
}
const requests = (operation: string) => vi.mocked(fetch).mock.calls.filter(([url]) => String(url).includes(operation))

describe('native account picker', () => {
  beforeEach(() => { setActivePinia(createPinia()) })
  afterEach(() => { useToastsStore().clear(); globalThis.fetch = originalFetch; document.body.innerHTML = ''; vi.useRealTimers() })

  it('renders Antigravity native windows through the operation caller and retains unavailable state independently', async () => {
    let reading: unknown = { plan: 'Antigravity Starter Quota', meters: [
      { scope: 'Gemini Models · Weekly', remaining_percent: 0, resets_at: '2099-01-01T00:00:00Z' },
      { scope: 'Claude and GPT models · Weekly', remaining_percent: 25, resets_at: '2099-01-02T00:00:00Z' },
    ] }
    globalThis.fetch = vi.fn(async url => String(url).includes('subscriptions')
      ? json({ checked_at: new Date().toISOString(), readings: { antigravity: reading, codex: { plan: 'ChatGPT Pro', meters: [] } } })
      : String(url).includes('adoptAccount') ? json({ data: a }) : json(emptyInventory())) as typeof fetch
    const store = useHomeSubscriptions()
    await store.load(true)
    const wrapper = panel()
    try {
      await flushPromises()
      const card = () => wrapper.get('li[aria-label="Antigravity"]')
      expect(card().get('img').attributes('src')).toBe('/images/antigravity.png')
      expect(card().text()).toContain('Connected')
      expect(card().text()).toContain('Antigravity Starter Quota')
      expect(card().text()).not.toContain('Plan unavailable')
      expect(card().findAll('[role="meter"]').map(meter => meter.attributes('aria-valuenow'))).toEqual(['100', '75'])
      expect(wrapper.findAllComponents(UiScoreMeter).map(meter => meter.props('tone'))).toEqual(['danger', 'warning'])
      expect(card().findAll('time').map(time => time.attributes('datetime'))).toEqual(['2099-01-01T00:00:00Z', '2099-01-02T00:00:00Z'])
      expect(card().find('[aria-haspopup="menu"]').exists()).toBe(false)
      reading = { ...(reading as object), plan: null }
      await store.load(true)
      await wrapper.setProps({ subscriptions: store.subscriptions })
      expect(card().text()).toContain('Plan unavailable')
      expect(card().text()).not.toContain('Antigravity Starter Quota')
      expect(card().findAll('[role="meter"]').map(meter => meter.attributes('aria-valuenow'))).toEqual(['100', '75'])
      reading = null
      await store.load(true)
      await wrapper.setProps({ subscriptions: store.subscriptions })
      expect(card().text()).toContain('Connected')
      expect(card().text()).toContain('Quota unavailable')
      expect(card().get('.host-resets').text()).toBe('N/A')
      expect(wrapper.get('li[aria-label="Codex"]').text()).toContain('ChatGPT Pro')
      reading = { plan: null, meters: [{ scope: 'Gemini Models · Weekly', remaining_percent: 100, resets_at: null }] }
      await store.load(true)
      await wrapper.setProps({ subscriptions: store.subscriptions, state: detection([]) })
      expect(card().text()).toContain('Not detected')
      expect(card().get('[role="meter"]').attributes('aria-valuenow')).toBe('0')
      expect(wrapper.findComponent(UiScoreMeter).props('tone')).toBe('success')
    } finally { wrapper.unmount() }
  })

  it.each([null, 'codex', 'claude-code', 'grok'])('keeps six optional cards and adopts only %s', async host => {
    globalThis.fetch = vi.fn(async url => String(url).includes('adoptAccount')
      ? json({ detail: 'Sign in', data: { code: 'identity_unverified', identity_status: 'needs_sign_in' } }, 400)
      : json(emptyInventory())) as typeof fetch
    const wrapper = panel()
    try {
      await wrapper.setProps({ state: detection(host ? [host] : []) })
      await flushPromises()
      expect(wrapper.findAll('.host-card')).toHaveLength(6)
      expect(requests('adoptAccount').map(([, init]) => JSON.parse(String(init?.body)).arguments.host_key)).toEqual(host ? [host] : [])
      expect(wrapper.find('[role="alert"]').exists()).toBe(false)
      if (host) {
        const name = host === 'grok' ? 'Grok Build' : host === 'codex' ? 'Codex' : 'Claude Code'
        await wrapper.get(`[aria-label="${name} account: Choose account"]`).trigger('click')
        expect(wrapper.get('[role="menu"]').text()).toContain('Add account')
      }
    } finally { wrapper.unmount() }
  })

  it.each(['refresh', 'availability'])('retries failed adoption only through %s and clears obsolete errors', async retry => {
    let fail = true
    globalThis.fetch = vi.fn(async url => String(url).includes('adoptAccount')
      ? fail ? json({ detail: 'Grok adoption failed' }, 503) : json({ data: a })
      : json(emptyInventory())) as typeof fetch
    const wrapper = panel()
    try {
      await wrapper.setProps({ state: detection(['grok']) }); await flushPromises()
      expect(wrapper.text()).toContain('Grok adoption failed')
      const count = requests('adoptAccount').length
      await wrapper.setProps({ summary: 'Unrelated render' }); await flushPromises()
      expect(requests('adoptAccount')).toHaveLength(count)
      fail = false
      if (retry === 'refresh') await wrapper.findAll('button').find(b => b.text() === 'Refresh')!.trigger('click')
      else {
        await wrapper.setProps({ state: detection([]) }); await flushPromises()
        expect(wrapper.text()).not.toContain('Grok adoption failed')
        await wrapper.setProps({ state: detection(['grok']) })
      }
      await flushPromises()
      expect(requests('adoptAccount')).toHaveLength(count + 1)
      expect(wrapper.find('[aria-label="Grok Build account: first@example.com"]').exists()).toBe(true)
      expect(wrapper.text()).not.toContain('Grok adoption failed')
    } finally { wrapper.unmount() }
  })

  it('shows a shared inventory failure once, retaining healthy card content', async () => {
    let fail = false
    globalThis.fetch = vi.fn(async () => fail ? json({ detail: 'Store unavailable' }, 503) : json(inventory())) as typeof fetch
    const wrapper = panel()
    try {
      await wrapper.setProps({ state: detection(['codex']) }); await flushPromises()
      fail = true
      await wrapper.findAll('button').find(b => b.text() === 'Refresh')!.trigger('click'); await flushPromises()
      expect(wrapper.text().match(/Accounts unavailable\. Try Refresh\./g)).toHaveLength(1)
      expect(wrapper.findAll('.host-card [role="alert"]')).toHaveLength(0)
      expect(wrapper.find('[aria-label="Codex account: first@example.com"]').exists()).toBe(true)
      fail = false
      await wrapper.findAll('button').find(b => b.text() === 'Refresh')!.trigger('click'); await flushPromises()
      expect(wrapper.text()).not.toContain('Accounts unavailable.')
    } finally { wrapper.unmount() }
  })

  it('forgets a removed native identity when the tool returns signed out', async () => {
    let signedOut = false
    globalThis.fetch = vi.fn(async url => String(url).includes('adoptAccount')
      ? signedOut ? json({ detail: 'Sign in', data: { code: 'identity_unverified', identity_status: 'needs_sign_in' } }, 400) : json({ data: a })
      : json(inventory([a], null))) as typeof fetch
    const wrapper = panel()
    try {
      await wrapper.setProps({ state: detection(['codex']) }); await flushPromises()
      expect(wrapper.find('[aria-label="Codex account: first@example.com"]').exists()).toBe(true)
      await wrapper.setProps({ state: detection([]) }); await flushPromises()
      signedOut = true
      await wrapper.setProps({ state: detection(['codex']) }); await flushPromises()
      expect(wrapper.find('[aria-label="Codex account: Choose account"]').exists()).toBe(true)
      expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    } finally { wrapper.unmount() }
  })

  it('pauses removed-host polling, resumes on return and keeps keyboard Cancel and cleanup evidence', async () => {
    vi.useFakeTimers()
    const pending = { enrollment_ref: 'retained-claude', status: 'pending', expires_at: new Date(Date.now() + 900_000).toISOString() }
    let value = pending
    globalThis.fetch = vi.fn(async url => {
      if (String(url).includes('completeAccountEnrollment')) return json({ data: value })
      if (String(url).includes('cancelAccountEnrollment')) { value = { ...pending, status: 'cancelled', cleanup_pending: true } as typeof pending; return json({ data: value }) }
      return json({ hosts: { 'claude-code': { accounts: [], desired: { account_ref: null, revision: 0 }, pending_enrollment: value } } })
    }) as typeof fetch
    const wrapper = panel()
    try {
      await wrapper.setProps({ state: detection([]) }); await flushPromises()
      expect(document.body.textContent).toContain('Sign-in checks paused')
      expect(document.querySelector('[role="dialog"] time')?.getAttribute('datetime')).toBe(pending.expires_at)
      expect([...document.querySelectorAll('[role="dialog"] button')].some(b => b.textContent?.trim() === 'Refresh')).toBe(true)
      await vi.advanceTimersByTimeAsync(6000); await flushPromises()
      expect(requests('completeAccountEnrollment')).toHaveLength(0)
      await wrapper.setProps({ state: detection(['claude-code']) }); await flushPromises()
      await vi.advanceTimersByTimeAsync(2000); await flushPromises()
      expect(requests('completeAccountEnrollment')).toHaveLength(1)
      await wrapper.setProps({ state: detection([]) }); await flushPromises()
      await vi.advanceTimersByTimeAsync(6000); await flushPromises()
      expect(requests('completeAccountEnrollment')).toHaveLength(1)
      const cancel = [...document.querySelectorAll<HTMLButtonElement>('button')].find(b => b.textContent?.trim() === 'Cancel')!
      cancel.focus()
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }))
      expect(document.querySelector('[role="dialog"]')?.contains(document.activeElement)).toBe(true)
      cancel.click(); await flushPromises()
      expect(requests('cancelAccountEnrollment')).toHaveLength(1)
      expect(document.body.textContent).toContain('Native sign-in cleanup is still pending')
      expect(document.querySelector('[role="dialog"]')).not.toBeNull()
    } finally { wrapper.unmount() }
  })

  it.each([false, true])('fences a held adoption after removal or unmount (unmount: %s)', async unmount => {
    let finish!: (response: Response) => void
    globalThis.fetch = vi.fn(async url => String(url).includes('adoptAccount')
      ? new Promise<Response>(resolve => { finish = resolve })
      : json({ hosts: { ...emptyInventory().hosts, ...inventory().hosts } })) as typeof fetch
    const wrapper = panel()
    try {
      await wrapper.setProps({ state: detection(['codex', 'grok']) }); await flushPromises()
      if (unmount) wrapper.unmount()
      else await wrapper.setProps({ state: detection(['codex']) })
      finish(json({ detail: 'Obsolete error' }, 503)); await flushPromises()
      expect(wrapper.text()).not.toContain('Obsolete error')
      expect(requests('adoptAccount')).toHaveLength(1)
      if (!unmount) expect(wrapper.find('[aria-label="Codex account: first@example.com"]').exists()).toBe(true)
    } finally { if (!unmount) wrapper.unmount() }
  })

  it('expires unavailable enrollment without polling and retries uncertain cleanup explicitly', async () => {
    vi.useFakeTimers()
    const pending = { enrollment_ref: 'expiring-claude', status: 'pending', expires_at: new Date(Date.now() + 3000).toISOString() }
    let cleaned = false
    globalThis.fetch = vi.fn(async url => {
      if (String(url).includes('cancelAccountEnrollment')) { cleaned = true; return json({ data: { ...pending, status: 'expired', cleanup_pending: false } }) }
      return json({ hosts: { 'claude-code': { accounts: [], desired: { account_ref: null, revision: 0 }, pending_enrollment: cleaned ? null : pending } } })
    }) as typeof fetch
    const wrapper = panel()
    try {
      await wrapper.setProps({ state: detection([]) }); await flushPromises()
      await vi.advanceTimersByTimeAsync(4000); await flushPromises()
      expect(requests('completeAccountEnrollment')).toHaveLength(0)
      expect(document.body.textContent).toContain('Sign-in expired')
      expect(document.body.textContent).toContain('Native sign-in cleanup is still pending')
      const retry = [...document.querySelectorAll<HTMLButtonElement>('button')].find(b => b.textContent?.trim() === 'Retry cleanup')!
      expect(retry.disabled).toBe(false)
      retry.click(); await flushPromises()
      expect(requests('cancelAccountEnrollment')).toHaveLength(1)
      expect(document.querySelector('[role="dialog"]')).toBeNull()
    } finally { wrapper.unmount() }
  })

  it('restores canonical pending_cleanups after cancel and remount until cleanup is confirmed', async () => {
    vi.useFakeTimers()
    const pending = { enrollment_ref: 'retired-claude', status: 'pending', expires_at: new Date(Date.now() + 900_000).toISOString(), cleanup_pending: false }
    let cancellations = 0
    globalThis.fetch = vi.fn(async (url, init) => {
      if (String(url).includes('cancelAccountEnrollment')) {
        expect(JSON.parse(String(init?.body)).arguments).toMatchObject({ host_key: 'claude-code', enrollment_ref: pending.enrollment_ref })
        cancellations++
        return json({ data: { ...pending, status: 'cancelled', cleanup_pending: cancellations < 3 } })
      }
      return json({ hosts: { ...inventory().hosts, 'claude-code': {
        accounts: [], desired: { account_ref: null, revision: 0 },
        pending_enrollment: cancellations === 0 ? pending : null,
        pending_cleanups: cancellations > 0 && cancellations < 3 ? [{ ...pending, status: 'cancelled', cleanup_pending: true }] : [],
      } } })
    }) as typeof fetch
    const clickDialog = async (label: string) => {
      const button = [...document.querySelectorAll<HTMLButtonElement>('[role="dialog"] button')].find(b => b.textContent?.trim() === label)!
      expect(button.disabled).toBe(false)
      button.click(); await flushPromises()
    }
    let wrapper = panel()
    try {
      await wrapper.setProps({ state: detection(['codex']) }); await flushPromises()
      await clickDialog('Cancel')
      expect(document.body.textContent).toContain('Native sign-in cleanup is still pending')
      wrapper.unmount()
      wrapper = panel()
      await wrapper.setProps({ state: detection(['codex']) }); await flushPromises()
      expect(document.body.textContent).toContain('Sign-in cancelled')
      expect(document.body.textContent).toContain('Native sign-in cleanup is still pending')
      expect(wrapper.find('[aria-label="Codex account: first@example.com"]').exists()).toBe(true)
      await vi.advanceTimersByTimeAsync(6000); await flushPromises()
      expect(requests('completeAccountEnrollment')).toHaveLength(0)
      await clickDialog('Retry cleanup')
      expect(document.body.textContent).toContain('Native sign-in cleanup is still pending')
      await clickDialog('Retry cleanup')
      expect(document.querySelector('[role="dialog"]')).toBeNull()
      expect(requests('cancelAccountEnrollment')).toHaveLength(3)
      expect(wrapper.find('[aria-label="Codex account: first@example.com"]').exists()).toBe(true)
    } finally { wrapper.unmount() }
  })

  it('restores every canonical cleanup before a separate pending enrollment', async () => {
    const pending = { enrollment_ref: 'new-codex', status: 'pending', expires_at: new Date(Date.now() + 900_000).toISOString() }
    const cleanups = ['old-claude', 'failed-claude'].map((enrollment_ref, index) => ({ ...pending, enrollment_ref, status: index ? 'failed' : 'cancelled', cleanup_pending: true }))
    globalThis.fetch = vi.fn(async (url, init) => {
      if (String(url).includes('cancelAccountEnrollment')) {
        const args = JSON.parse(String(init?.body)).arguments
        expect(args).toMatchObject({ host_key: 'claude-code', enrollment_ref: cleanups[0]!.enrollment_ref })
        return json({ data: { ...cleanups.shift(), cleanup_pending: false } })
      }
      return json({ hosts: {
        codex: { ...inventory().hosts.codex, pending_enrollment: pending, pending_cleanups: [] },
        'claude-code': { accounts: [], desired: { account_ref: null, revision: 0 }, pending_enrollment: null, pending_cleanups: [...cleanups] },
      } })
    }) as typeof fetch
    const wrapper = panel()
    try {
      await wrapper.setProps({ state: detection(['codex']) }); await flushPromises()
      for (let index = 0; index < 2; index++) {
        expect(document.body.textContent).toContain('Native sign-in cleanup is still pending')
        const retry = [...document.querySelectorAll<HTMLButtonElement>('[role="dialog"] button')].find(b => b.textContent?.trim() === 'Retry cleanup')!
        retry.click(); await flushPromises()
      }
      expect(document.body.textContent).not.toContain('Native sign-in cleanup is still pending')
      expect(document.body.textContent).toContain('Complete sign-in in the native browser')
      expect(document.querySelector('[role="dialog"] time')?.getAttribute('datetime')).toBe(pending.expires_at)
      expect(requests('cancelAccountEnrollment')).toHaveLength(2)
      expect(wrapper.find('[aria-label="Codex account: first@example.com"]').exists()).toBe(true)
    } finally { wrapper.unmount() }
  })

  it('retires a held completion on unmount without restarting polling', async () => {
    vi.useFakeTimers()
    const pending = { enrollment_ref: 'unmounted', status: 'pending', expires_at: new Date(Date.now() + 900_000).toISOString() }
    let finish!: (response: Response) => void
    globalThis.fetch = vi.fn(async url => String(url).includes('completeAccountEnrollment')
      ? new Promise<Response>(resolve => { finish = resolve })
      : json({ hosts: { grok: { accounts: [], desired: { account_ref: null, revision: 0 }, pending_enrollment: pending } } })) as typeof fetch
    const wrapper = panel()
    await wrapper.setProps({ state: detection(['grok']) }); await flushPromises()
    await vi.advanceTimersByTimeAsync(2000); await flushPromises()
    wrapper.unmount()
    finish(json({ data: pending })); await flushPromises()
    await vi.advanceTimersByTimeAsync(10000); await flushPromises()
    expect(requests('completeAccountEnrollment')).toHaveLength(1)
    expect(document.querySelector('[role="dialog"]')).toBeNull()
  })

  it('does not let a held completion revive cancelled enrollment or polling', async () => {
    vi.useFakeTimers()
    const pending = { enrollment_ref: 'held-grok', status: 'pending', expires_at: new Date(Date.now() + 900_000).toISOString() }
    let finish!: (response: Response) => void
    let cancelled = false
    globalThis.fetch = vi.fn(async url => {
      if (String(url).includes('adoptAccount')) return json({ detail: 'Sign in', data: { code: 'identity_unverified', identity_status: 'needs_sign_in' } }, 400)
      if (String(url).includes('completeAccountEnrollment')) return new Promise<Response>(resolve => { finish = resolve })
      if (String(url).includes('cancelAccountEnrollment')) { cancelled = true; return json({ data: { ...pending, status: 'cancelled' } }) }
      return json({ hosts: { grok: { accounts: [], desired: { account_ref: null, revision: 0 }, pending_enrollment: cancelled ? null : pending } } })
    }) as typeof fetch
    const wrapper = panel()
    try {
      await wrapper.setProps({ state: detection(['grok']) }); await flushPromises()
      await vi.advanceTimersByTimeAsync(2000); await flushPromises()
      await wrapper.setProps({ state: detection([]) }); await flushPromises()
      const cancel = [...document.querySelectorAll<HTMLButtonElement>('button')].find(b => b.textContent?.trim() === 'Cancel')!
      expect(cancel.disabled).toBe(false)
      cancel.click(); await flushPromises()
      expect(document.querySelector('[role="dialog"]')).toBeNull()
      finish(json({ data: pending })); await flushPromises()
      await wrapper.setProps({ state: detection(['grok']) }); await flushPromises()
      await vi.advanceTimersByTimeAsync(6000); await flushPromises()
      expect(document.querySelector('[role="dialog"]')).toBeNull()
      expect(requests('completeAccountEnrollment')).toHaveLength(1)
    } finally { wrapper.unmount() }
  })

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
      expect(wrapper.get('li[aria-label="Antigravity"]').find('[aria-haspopup="menu"]').exists()).toBe(false)
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
