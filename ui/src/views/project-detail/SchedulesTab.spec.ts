// Smoke tests for SchedulesTab.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'

import SchedulesTab from './SchedulesTab.vue'

const ORIG_FETCH = globalThis.fetch

function mountTab() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: '/projects/:id/schedules',
        name: 'project-detail-schedules',
        component: SchedulesTab,
      },
    ],
  })
  void router.push('/projects/1/schedules')
  return router
}

describe('SchedulesTab', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })
  afterEach(() => {
    globalThis.fetch = ORIG_FETCH
    vi.restoreAllMocks()
  })

  it('renders empty state + add-schedule button', async () => {
    globalThis.fetch = vi.fn(async () => {
      return new Response(JSON.stringify([]), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    }) as typeof fetch
    const router = mountTab()
    await router.isReady()
    const w = mount(SchedulesTab, { global: { plugins: [router] } })
    await new Promise((r) => setTimeout(r, 0))
    expect(w.text()).toContain('Scheduled jobs')
    expect(w.findAll('button').some((button) => button.text() === 'Add schedule')).toBe(false)
    expect(w.text()).toContain('Agent-owned schedules will appear here')
  })
})
