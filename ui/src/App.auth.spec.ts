import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import App from './App.vue'
import { registerAuthStoreWithClient, useAuthStore } from '@/stores/auth'
import { apiFetch, clearAuthStore } from '@/lib/client'
import { useProjectsStore } from '@/stores/projects'

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

it('never restores a previous user’s cached heading or sidebar while fresh navigation is delayed', async () => {
  const pinia = createPinia(); setActivePinia(pinia)
  const auth = useAuthStore(); auth.mode = 'server'; auth.state = {kind:'ready'}
  registerAuthStoreWithClient(auth)
  const projects = useProjectsStore()
  projects.items = [{id:7,name:'U1 private project',slug:'u1',domain:'example.test',locale:'en-US',niche:null,
    is_active:true,order_index:1,schedule_json:null,created_at:'2026-11-10T00:00:00Z',updated_at:'2026-11-10T00:00:00Z'}]
  projects.activeProjectId = 7
  const router = createRouter({history:createMemoryHistory(),routes:[{
    path:'/projects/7',component:{template:'<h1>U1 private project</h1>'},
  }]})
  await router.push('/projects/7')
  const json = (body: unknown, status = 200) => new Response(JSON.stringify(body),{status,headers:{'content-type':'application/json'}})
  let completeLogin!: (response: Response) => void
  vi.stubGlobal('fetch',vi.fn(async (path) => {
    if (path === '/api/v1/projects') return json({},401)
    if (path === '/api/v1/auth/login') return new Promise<Response>(resolve => {completeLogin = resolve})
    return json({items:[]})
  }))
  const wrapper = mount(App,{global:{plugins:[pinia,router]}})
  expect(wrapper.get('h1').text()).toBe('U1 private project')
  await expect(apiFetch('/api/v1/projects')).rejects.toThrow()
  await flushPromises()
  expect(wrapper.find('aside').exists()).toBe(false)
  const login = auth.submit('login',{email:'u2@example.test',password:'synthetic-password'})
  await flushPromises()
  expect(wrapper.text()).not.toContain('U1 private project')
  completeLogin(json({status:'authenticated',user_id:'u2',environment_id:'A',csrf_token:'u2-csrf'}))
  await login; await flushPromises()
  // Hold this document open, as a delayed root response does in a browser.
  // Keeping the old project cached deliberately makes premature readiness visible.
  expect(projects.items[0]!.name).toBe('U1 private project')
  expect(auth.ready).toBe(false)
  expect(wrapper.text()).not.toContain('U1 private project')
  expect(wrapper.find('aside').exists()).toBe(false)
  wrapper.unmount()
})
