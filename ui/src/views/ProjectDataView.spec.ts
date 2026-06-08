import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'

import ProjectDataView from './ProjectDataView.vue'

const ORIG_FETCH = globalThis.fetch

describe('ProjectDataView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
  })

  afterEach(() => {
    globalThis.fetch = ORIG_FETCH
    vi.restoreAllMocks()
  })

  it('opens the tab requested by the route query', async () => {
    globalThis.fetch = vi.fn(async () => json({ items: [], next_cursor: null })) as typeof fetch

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/data', component: ProjectDataView }],
    })
    await router.push('/projects/1/data?tab=artifacts')
    await router.isReady()

    const wrapper = mount(
      { template: '<RouterView />' },
      {
        global: {
          plugins: [router, createPinia()],
        },
      },
    )

    await vi.waitFor(() => expect(wrapper.find('section').text()).toContain('No artifacts.'))
    expect(wrapper.text()).not.toContain('No timeline events.')
  })
})

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  })
}
