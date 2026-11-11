import { afterEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { computed, ref } from 'vue'
import { TaskDeliveryStateOutStatus } from '@/api'
import { createMemoryHistory, createRouter } from 'vue-router'
import { usageFeedKey } from '@/composables/useUsageFeed'
import type { UsageFeedState } from '@/lib/usageUpdates'
import { activeTaskFixture } from './activeTaskFixtures'
import ActiveTaskRow from './ActiveTaskRow.vue'

afterEach(() => { document.body.innerHTML = '' })

describe('continuous Home task rows', () => {
  it('uses canonical status, current and Last identities, measured zero and unknown values', async () => {
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/:pathMatch(.*)*', component: { template: '<div />' } }] })
    await router.push('/')
    const feed = ref<UsageFeedState>({ status: 'live', receivedAt: Date.now() })
    const task = activeTaskFixture({ delivery: { status: TaskDeliveryStateOutStatus.running, ticketKey: 'child', reason: null },
      active_execution: { invocation_id: 'actual-fixture', ticket_key: 'child', agent: 'sdlc_delivery', host_key: 'codex', model: 'exact-recorded-model', started_at: '2026-11-11T19:00:00Z', live_usage: null } })
    const wrapper = mount(ActiveTaskRow, { props: { task, global: true }, global: { plugins: [router], provide: { [usageFeedKey as symbol]: computed(() => feed.value) } } })
    try {
      expect(wrapper.text()).toContain('Starting')
      expect(wrapper.text()).toContain('Live')
      expect(wrapper.text()).toContain('2/7')
      expect(wrapper.text()).toContain('exact-recorded-model')
      expect(wrapper.get('a').attributes('aria-label')).toContain('Recorded task goal')
      feed.value = { status: 'reconnecting', receivedAt: Date.now() }
      await wrapper.vm.$nextTick()
      expect(wrapper.find('.usage-live-indicator--live').exists()).toBe(false)
      expect(wrapper.text()).toContain('Reconnecting')
      task.agent_stats!.last_execution = { agent: 'sdlc_review', host_key: 'codex', model: 'last-exact', started_at: '2026-11-11T18:00:00Z', invocation_id: null, ticket_key: 'child' }
      await wrapper.setProps({ task: { ...task, active_execution: null, delivery: { status: TaskDeliveryStateOutStatus.stopped, ticketKey: 'child', reason: 'Required verification blocked' } } })
      expect(wrapper.text()).toContain('Last: Review')
      expect(wrapper.text()).toContain('Required verification blocked')
      expect(wrapper.findAll('.ui-badge').filter(badge => badge.text() === 'Stopped')).toHaveLength(1)
      expect(wrapper.find('.usage-live-indicator').exists()).toBe(false)
      await wrapper.setProps({ task: activeTaskFixture({ agent_stats: null }) })
      expect(wrapper.findAll('dd').map(value => value.text())).toEqual(['N/A', 'N/A', 'N/A'])
      const zero = activeTaskFixture()
      zero.agent_stats!.reported_usage = { ...zero.agent_stats!.reported_usage, input: 0, remaining_input: 0, cache_read: 0, output: 0 }
      await wrapper.setProps({ task: zero })
      expect(wrapper.findAll('dd').map(value => value.text())).toEqual(['0', '0', '0'])
      await wrapper.setProps({ task: activeTaskFixture({ delivery: null, agent_stats: null, owner: 'Manual owner' }) })
      expect(wrapper.text()).not.toContain('Automated')
      expect(wrapper.find('.agent-usage-summary').exists()).toBe(false)
      expect(wrapper.text()).toContain('Assigned: Manual owner')
      expect(wrapper.text()).not.toContain('active-work')
    } finally { wrapper.unmount() }
  })
})
