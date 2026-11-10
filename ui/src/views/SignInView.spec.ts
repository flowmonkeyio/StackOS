import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import SignInView from './SignInView.vue'
import { registerAuthStoreWithClient, useAuthStore } from '@/stores/auth'
import { clearAuthStore } from '@/lib/client'

afterEach(() => { vi.unstubAllGlobals(); clearAuthStore() })

it('renders register, resend, recovery and password reset through StackOS', async () => {
  const pinia = createPinia()
  setActivePinia(pinia)
  const auth = useAuthStore()
  auth.mode = 'server'
  auth.state = { kind: 'signed_out' }
  registerAuthStoreWithClient(auth)
  const calls: { path: string; init?: RequestInit }[] = []
  vi.stubGlobal('fetch', vi.fn(async (path, init) => {
    calls.push({ path, init })
    return new Response('{}', { headers: { 'content-type': 'application/json' } })
  }))
  const wrapper = mount(SignInView, { global: { plugins: [pinia] } })
  const click = async (text: string) => {
    await wrapper.findAll('button').find((b) => b.text() === text)!.trigger('click')
  }
  await click('Create account')
  await wrapper.get('input[type=email]').setValue('u1@example.test')
  await wrapper.get('input[type=password]').setValue('synthetic-password')
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(wrapper.text()).toContain('Check your email for a verification link')
  expect(wrapper.get<HTMLInputElement>('input[type=password]').element.value).toBe('')
  await click('Resend verification email')
  await wrapper.get('form').trigger('submit'); await flushPromises()
  await click('Forgot password?')
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(wrapper.text()).toContain('password reset link')
  auth.state = { kind: 'recovery' }; auth.csrfToken = 'recovery-csrf'
  await flushPromises()
  expect(wrapper.find('input[type=email]').exists()).toBe(false)
  await wrapper.get('input[type=password]').setValue('new-synthetic-password')
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(wrapper.text()).toContain('Password updated. Sign in')
  expect(calls.map((c) => c.path)).toEqual(['/api/v1/auth/register', '/api/v1/auth/resend', '/api/v1/auth/recover', '/api/v1/auth/password'])
  expect(new Headers(calls[3]!.init?.headers).get('X-CSRF-Token')).toBe('recovery-csrf')
  wrapper.unmount()
})

it('shows a retryable Auth error and clears the submitted password', async () => {
  const pinia = createPinia(); setActivePinia(pinia)
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: { code: 'retry_later' } }), { status: 429, headers: { 'content-type': 'application/json' } })))
  const wrapper = mount(SignInView, { global: { plugins: [pinia] } })
  await wrapper.get('input[type=email]').setValue('u1@example.test')
  await wrapper.get('input[type=password]').setValue('synthetic-password')
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(wrapper.text()).toContain('Wait before trying again')
  expect(wrapper.get<HTMLInputElement>('input[type=password]').element.value).toBe('')
  wrapper.unmount()
})

it('returns an expired recovery session to sign-in and email recovery controls', async () => {
  const pinia = createPinia(); setActivePinia(pinia)
  const auth = useAuthStore(); auth.mode = 'server'; auth.state = {kind:'recovery'}
  vi.stubGlobal('fetch',vi.fn(async()=>new Response(JSON.stringify({detail:{code:'session_expired_or_invalid'}}),{status:401,headers:{'content-type':'application/json'}})))
  const wrapper = mount(SignInView,{global:{plugins:[pinia]}})
  await wrapper.get('input[type=password]').setValue('synthetic-password')
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(wrapper.text()).toContain('Your session has expired')
  expect(wrapper.find('input[type=email]').exists()).toBe(true)
  expect(wrapper.text()).toContain('Forgot password?')
  wrapper.unmount()
})
