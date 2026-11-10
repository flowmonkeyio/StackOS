import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import App from './App.vue'
import { registerAuthStoreWithClient, useAuthStore } from '@/stores/auth'
import { apiFetch, clearAuthStore } from '@/lib/client'

afterEach(() => { vi.unstubAllGlobals(); clearAuthStore() })

it.each(['signed_out', 'recovery'] as const)('gates private route rendering and requests during %s', async (kind) => {
  const pinia = createPinia(); setActivePinia(pinia)
  const auth = useAuthStore(); auth.mode = 'server'; auth.state = { kind }
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path:'/projects/7', component: { template:'<p>Private data</p>' } }] })
  await router.push('/projects/7')
  const fetch = vi.fn(); vi.stubGlobal('fetch', fetch)
  const wrapper = mount(App,{ global:{plugins:[pinia,router]} })
  await flushPromises()
  expect(wrapper.text()).not.toContain('Private data')
  expect(wrapper.find('aside').exists()).toBe(false)
  expect(fetch).not.toHaveBeenCalled()
  wrapper.unmount()
})

it('removes an open private view after a rejected authenticated request', async () => {
  const pinia = createPinia(); setActivePinia(pinia)
  const auth = useAuthStore(); auth.mode = 'server'; auth.state = {kind:'ready'}
  registerAuthStoreWithClient(auth)
  const router = createRouter({history:createMemoryHistory(),routes:[{path:'/',component:{template:'<p>Private data</p>'}}]})
  await router.push('/')
  vi.stubGlobal('fetch',vi.fn(async()=>new Response('{}',{status:401,headers:{'content-type':'application/json'}})))
  const wrapper = mount(App,{global:{plugins:[pinia,router]}})
  expect(wrapper.text()).toContain('Private data')
  await expect(apiFetch('/api/v1/projects')).rejects.toThrow()
  await flushPromises()
  expect(wrapper.text()).not.toContain('Private data')
  expect(wrapper.text()).toContain('Sign in')
  wrapper.unmount()
})
