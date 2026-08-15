import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent, nextTick, onMounted } from 'vue'
import { createMemoryHistory, createRouter, useRoute } from 'vue-router'

import App from './App.vue'
import { useProjectsStore, type Project } from '@/stores/projects'

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
    projects.items = [projectFixture(1, 'StackOS Local')]
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
    projects.items = [projectFixture(1, 'StackOS Local')]
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

  it('keeps the current project surface and remounts it under the selected scope', async () => {
    const projects = useProjectsStore()
    projects.items = [projectFixture(1, 'Alpha'), projectFixture(2, 'Beta')]
    projects.activeProjectId = 1
    const mountedProjectIds: number[] = []
    const ProjectProbe = defineComponent({
      setup() {
        const route = useRoute()
        const projectId = Number.parseInt(String(route.params.id), 10)
        onMounted(() => mountedProjectIds.push(projectId))
        return { projectId }
      },
      template: '<h1>Connections {{ projectId }}</h1>',
    })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        {
          path: '/projects/:id/connections',
          name: 'project-connections',
          component: ProjectProbe,
        },
      ],
    })
    await router.push('/projects/1/connections?section=bots&provider_key=telegram')
    await router.isReady()

    const wrapper = mount(App, {
      global: {
        plugins: [router],
        stubs: {
          DesktopUpdatePrompt: true,
          PluginNavRenderer: { template: '<nav />' },
          ProjectSwitcher: {
            emits: ['select'],
            template:
              '<button data-test="switch-project" @click="$emit(\'select\', 2)">Beta</button>',
          },
        },
      },
    })
    await flushPromises()
    await wrapper.get('[data-test="switch-project"]').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.fullPath).toBe('/projects/2/connections?section=bots')
    expect(projects.activeProjectId).toBe(2)
    expect(mountedProjectIds).toEqual([1, 2])
    expect(wrapper.get('h1').text()).toBe('Connections 2')
  })

})

function projectFixture(id: number, name: string): Project {
  return {
    id,
    name,
    slug: name.toLowerCase(),
    domain: `${name.toLowerCase()}.test`,
    niche: 'platform',
    locale: 'en-US',
    is_active: true,
    schedule_json: null,
    created_at: '2026-06-01T00:00:00Z',
    updated_at: '2026-06-01T00:00:00Z',
  }
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  })
}
