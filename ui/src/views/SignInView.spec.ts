import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import SignInView from './SignInView.vue'
import { authDestination, registerAuthStoreWithClient, useAuthStore } from '@/stores/auth'
import { clearAuthStore } from '@/lib/client'

afterEach(() => { vi.unstubAllGlobals(); clearAuthStore(); Reflect.deleteProperty(window, 'stackosDesktop'); localStorage.clear(); window.history.replaceState({}, '', '/'); document.body.innerHTML = '' })
async function setup(url = '/sign-in', recovery = false) {
  window.history.replaceState({}, '', url)
  const pinia = createPinia()
  const auth = useAuthStore(pinia)
  auth.mode = 'server'; auth.state = { kind: recovery ? 'recovery' : 'signed_out' }; auth.csrfToken = recovery ? 'reset-csrf' : null
  registerAuthStoreWithClient(auth)
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/sign-in', component: SignInView }] })
  await router.push(url)
  const wrapper = mount(SignInView, { attachTo: document.body, global: { plugins: [pinia, router] } })
  const click = async (text: string) => { await wrapper.findAll('button').find(b => b.text() === text)!.trigger('click'); await flushPromises() }
  return { wrapper, auth, click }
}
function json(body: unknown, status = 200) { return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }) }

it('keeps return destinations inside allowed app routes on the exact selected origin', () => {
  expect(authDestination('/projects/7/tasks?task=receipt')).toBe('/projects/7/tasks?task=receipt')
  for (const target of ['//evil.test/projects/7', 'https://evil.test', '/sign-in?returnTo=evil', '/api/v1/auth/callback', '/\\evil.test', 'javascript:alert(1)']) expect(authDestination(target)).toBe('/')
})

it('keeps check-email, resend and change-email separate from password entry', async () => {
  const fetcher = vi.fn(async (_path: string, _init?: RequestInit) => json({})); vi.stubGlobal('fetch', fetcher)
  const { wrapper, click } = await setup()
  await click('Create an account')
  expect(document.activeElement?.id).toBe('account-heading')
  await wrapper.get('#account-email').setValue('u1@example.test')
  await wrapper.get('#account-password').setValue('synthetic-password')
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(wrapper.get('h1').text()).toBe('Check your email')
  expect(document.activeElement?.id).toBe('account-heading')
  expect(wrapper.text()).toContain('u1@example.test')
  expect(wrapper.find('input[type=password]').exists()).toBe(false)
  await click('Resend email')
  expect(wrapper.text()).toContain('Email sent.')
  await click('Use a different email')
  expect(document.activeElement?.id).toBe('account-heading')
  expect(wrapper.get<HTMLInputElement>('#account-email').element.value).toBe('u1@example.test')
  expect(wrapper.get<HTMLInputElement>('#account-password').element.value).toBe('')
  expect(fetcher.mock.calls.map(c => c[0])).toEqual(['/api/v1/auth/register', '/api/v1/auth/resend'])
  wrapper.unmount()
})

it('removes the one-time hash from history and waits for explicit confirmation', async () => {
  const fetcher = vi.fn(async (_path: string, _init?: RequestInit) => json({ status: 'authenticated', user_id: 'u1', environment_id: 'A', csrf_token: 'csrf' })); vi.stubGlobal('fetch', fetcher)
  const { wrapper } = await setup('/sign-in?auth=callback&purpose=signup&outcome=pending&token_hash=one-time')
  expect(window.location.search).not.toContain('one-time')
  expect(fetcher).not.toHaveBeenCalled()
  expect(wrapper.get('h1').text()).toBe('Verify your email')
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(fetcher).toHaveBeenCalledTimes(1)
  expect(JSON.parse(String(fetcher.mock.calls[0]![1]!.body))).toEqual({ token_hash: 'one-time', type: 'signup' })
  expect(wrapper.get('h1').text()).toBe('Email verified')
  expect(wrapper.text()).not.toContain('Open StackOS')
  wrapper.unmount()
})

it.each(['signup', 'recovery'])('renews expired/used %s links with the correct endpoint', async purpose => {
  const fetcher = vi.fn(async (_path: string, _init?: RequestInit) => json({})); vi.stubGlobal('fetch', fetcher)
  const { wrapper } = await setup(`/sign-in?auth=callback&purpose=${purpose}&outcome=expired_or_used`)
  expect(wrapper.text()).toContain('expired or was used')
  await wrapper.get('#account-email').setValue('u1@example.test')
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(fetcher.mock.calls[0]![0]).toBe(`/api/v1/auth/${purpose === 'signup' ? 'resend' : 'recover'}`)
  expect(wrapper.get('h1').text()).toBe('Check your email')
  wrapper.unmount()
})

it('supports reveal/autofill, associated errors, retry feedback and keyboard focus', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json({ detail: { code: 'retry_later' } }, 429)))
  const { wrapper } = await setup()
  await wrapper.get('[aria-label="Show password"]').trigger('click')
  expect(wrapper.get('#account-password').attributes('type')).toBe('text')
  expect(wrapper.get('#account-password').attributes('autocomplete')).toBe('current-password')
  await wrapper.get('#account-email').setValue('u1@example.test')
  await wrapper.get('#account-password').setValue('synthetic-password')
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(wrapper.get('#account-password').attributes('aria-invalid')).toBe('true')
  expect(wrapper.get('#account-password').element.closest('.ui-input')?.classList.contains('border-danger')).toBe(true)
  expect(wrapper.get('#account-password').attributes('aria-describedby')).toContain('account-password-err')
  expect(wrapper.text()).toContain('Wait a moment')
  expect(document.activeElement?.id).toBe('account-password')
  expect(wrapper.text().match(/Too many attempts/g)).toHaveLength(1)
  expect(wrapper.get<HTMLInputElement>('#account-password').element.value).toBe('')
  wrapper.unmount()
})

it('completes a recovery session without admitting private content', async () => {
  const fetcher = vi.fn(async (_path: string, _init?: RequestInit) => json({})); vi.stubGlobal('fetch', fetcher)
  const { wrapper, auth } = await setup('/sign-in', true)
  expect(wrapper.get('h1').text()).toBe('Choose a new password')
  await wrapper.get('#account-password').setValue('new-synthetic-password')
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(wrapper.get('h1').text()).toBe('Password updated')
  expect(document.activeElement?.id).toBe('account-heading')
  expect(auth.ready).toBe(false)
  expect(new Headers(fetcher.mock.calls[0]![1]!.headers).get('X-CSRF-Token')).toBe('reset-csrf')
  wrapper.unmount()
})

it('selects and focuses the password form after a deferred recovery reconnect', async () => {
  let complete!: (response: Response) => void
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(resolve => { complete = resolve })))
  const { wrapper, auth, click } = await setup()
  auth.state = { kind: 'error', message: 'Connection interrupted.' }
  await flushPromises()
  await click('Try again')
  expect(wrapper.get('h1').text()).toBe('Reconnecting to StackOS')
  expect(wrapper.find('#account-password').exists()).toBe(false)
  complete(json({ status: 'recovery_required', user_id: 'u1', environment_id: 'A', csrf_token: 'restored-csrf' }))
  await flushPromises()
  expect(wrapper.get('h1').text()).toBe('Choose a new password')
  expect(wrapper.get('#account-password').attributes('autocomplete')).toBe('new-password')
  expect(document.activeElement?.id).toBe('account-heading')
  expect(auth.ready).toBe(false)
  expect(auth.csrfToken).toBe('restored-csrf')
  wrapper.unmount()
})

it('focuses recovery confirmation and completion after their asynchronous actions', async () => {
  let complete!: (response: Response) => void
  const fetcher = vi.fn(() => new Promise<Response>(resolve => { complete = resolve }))
  vi.stubGlobal('fetch', fetcher)
  const { wrapper, auth } = await setup('/sign-in?auth=callback&purpose=recovery&outcome=pending&token_hash=one-time')
  expect(wrapper.get('h1').text()).toBe('Reset your password')
  await wrapper.get('form').trigger('submit')
  expect(wrapper.find('#account-password').exists()).toBe(false)
  complete(json({ status: 'recovery_required', user_id: 'u1', environment_id: 'A', csrf_token: 'reset-csrf' }))
  await flushPromises()
  expect(wrapper.get('h1').text()).toBe('Choose a new password')
  expect(document.activeElement?.id).toBe('account-heading')
  expect(auth.ready).toBe(false)
  await wrapper.get('#account-password').setValue('new-synthetic-password')
  await wrapper.get('form').trigger('submit')
  complete(json({}))
  await flushPromises()
  expect(wrapper.get('h1').text()).toBe('Password updated')
  expect(document.activeElement?.id).toBe('account-heading')
  expect(auth.ready).toBe(false)
  wrapper.unmount()
})

it('shows desktop return only for explicit desktop-origin context, with browser fallback', async () => {
  vi.stubGlobal('fetch', vi.fn())
  localStorage.setItem('stackos-account-return', JSON.stringify({purpose:'signup',until:Date.now()+60_000}))
  const { wrapper, click } = await setup('/sign-in?auth=callback&purpose=signup&outcome=success')
  expect(wrapper.text()).toContain('Open StackOS')
  expect(wrapper.text()).toContain('Applications')
  await click('Continue in this browser')
  expect(wrapper.text()).not.toContain('Open StackOS')
  expect(wrapper.text()).toContain('Continue to StackOS')
  wrapper.unmount()
})

it('uses the canonical native bridge for finite external-browser account entry', async () => {
  const openAccount = vi.fn(async () => undefined)
  Object.defineProperty(window, 'stackosDesktop', {configurable:true,value:{status:vi.fn(),openAccount}})
  const {wrapper,click} = await setup()
  await click('Create an account')
  await click('Forgot password?')
  expect(openAccount.mock.calls).toEqual([['register'],['recover']])
  expect(wrapper.get('h1').text()).toBe('Welcome back')
  wrapper.unmount()
})
