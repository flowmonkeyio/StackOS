import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'

import App from './App.vue'
import { useProjectsStore } from '@/stores/projects'

const ORIG_FETCH = globalThis.fetch

describe('App', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    globalThis.fetch = vi.fn(async () => json([])) as typeof fetch
  })

  afterEach(() => {
    globalThis.fetch = ORIG_FETCH
    vi.restoreAllMocks()
  })

  it('closes the mobile navigation drawer on Escape', async () => {
    const projects = useProjectsStore()
    projects.items = [
      {
        id: 1,
        name: 'StackOS Local',
        slug: 'stackos-local',
        domain: 'local.stackos',
        niche: 'platform',
        locale: 'en-US',
        is_active: true,
        schedule_json: null,
        created_at: '2026-06-01T00:00:00Z',
        updated_at: '2026-06-01T00:00:00Z',
      },
    ] as never
    projects.activeProjectId = 1

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/overview', component: { template: '<div />' } }],
    })
    await router.push('/projects/1/overview')
    await router.isReady()

    const wrapper = mount(App, {
      global: {
        plugins: [router],
        stubs: {
          PluginNavRenderer: { template: '<nav />' },
          ProjectSwitcher: { template: '<div />' },
        },
      },
    })
    await flushPromises()

    const sidebar = wrapper.get('#cs-sidebar')
    expect(sidebar.classes()).toContain('-translate-x-full')

    await wrapper.get('button[aria-label="Toggle navigation"]').trigger('click')
    expect(sidebar.classes()).toContain('translate-x-0')

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await nextTick()

    expect(sidebar.classes()).toContain('-translate-x-full')
  })

  it('shows navigation for the selected project while the user is on StackOS home', async () => {
    const projects = useProjectsStore()
    projects.items = [
      {
        id: 1,
        name: 'StackOS Local',
        slug: 'stackos-local',
        domain: 'local.stackos',
        niche: 'platform',
        locale: 'en-US',
        is_active: true,
        schedule_json: null,
        created_at: '2026-06-01T00:00:00Z',
        updated_at: '2026-06-01T00:00:00Z',
      },
    ] as never
    projects.activeProjectId = 1

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', component: { template: '<div>StackOS home</div>' } }],
    })
    await router.push('/')
    await router.isReady()

    const wrapper = mount(App, {
      global: {
        plugins: [router],
        stubs: {
          PluginNavRenderer: {
            props: ['sections'],
            template: '<nav data-test="project-navigation">{{ sections.length }}</nav>',
          },
          ProjectSwitcher: { template: '<div>StackOS Local</div>' },
        },
      },
    })
    await flushPromises()

    expect(wrapper.get('[data-test="project-navigation"]').text()).not.toBe('0')
    expect(wrapper.text()).not.toContain('Open a project to see its operating navigation')
  })
})

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  })
}
