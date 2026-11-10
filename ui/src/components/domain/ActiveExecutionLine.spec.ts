import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'
import { usageFeedKey } from '@/composables/useUsageFeed'
import type { UsageFeedState } from '@/lib/usageUpdates'
import ActiveExecutionLine from './ActiveExecutionLine.vue'

const execution = { live_usage: null, invocation_id: 'call-1', agent: 'Project_delivery_agent_with_a_long_name', host_key: 'codex', model: 'gpt-6.1-sol', started_at: '2026-11-08T01:08:00Z', ticket_key: 'child' }

describe('ActiveExecutionLine', () => {
  afterEach(() => vi.useRealTimers())

  it('uses a fresh initial now, then shared heartbeat invalidation without an agent or tokens', async () => {
    vi.useFakeTimers().setSystemTime(new Date('2026-11-08T01:22:00Z'))
    const feed = ref<UsageFeedState>({ status: 'paused', receivedAt: Date.parse('2026-11-08T01:00:00Z') })
    const item = { status: 'in-progress' as const, started_at: '2026-11-08T01:08:00', completed_at: null }
    const wrapper = mount(ActiveExecutionLine, { props: { item }, global: { provide: { [usageFeedKey as symbol]: feed } } })
    expect(wrapper.get('[aria-label="Elapsed time: 14m"]').text()).toBe('14m')
    expect(wrapper.find('img').exists()).toBe(false)
    expect(vi.getTimerCount()).toBe(0)
    vi.setSystemTime(new Date('2026-11-08T01:23:00Z'))
    feed.value = { status: 'live', receivedAt: Date.now() }
    await wrapper.vm.$nextTick()
    expect(wrapper.get('.active-execution-line__elapsed').text()).toBe('15m')
    expect(wrapper.find('.usage-live-indicator').exists()).toBe(false)
    await wrapper.setProps({ item: { ...item, status: 'complete', completed_at: '2026-11-08T01:22:00Z' } })
    vi.setSystemTime(new Date('2026-11-09T01:23:00Z'))
    feed.value = { status: 'live', receivedAt: Date.now() }
    await wrapper.vm.$nextTick()
    expect(wrapper.get('.active-execution-line__elapsed').text()).toBe('14m')
    await wrapper.setProps({ item })
    expect(wrapper.get('.active-execution-line__elapsed').text()).toBe('1d')
    wrapper.unmount()
  })

  it.each(['complete', 'deferred', 'aborted', 'failed', 'skipped'] as const)('retains %s elapsed across remount without identity and rejects missing ends', async status => {
    const props = { item: { status, started_at: '2026-11-08T01:00:00Z', completed_at: '2026-11-08T03:22:00Z' } }
    let wrapper = mount(ActiveExecutionLine, { props })
    expect(wrapper.get('.active-execution-line__elapsed').text()).toBe('2h 22m')
    wrapper.unmount()
    wrapper = mount(ActiveExecutionLine, { props })
    expect(wrapper.get('.active-execution-line__elapsed').attributes('title')).toBe('Elapsed time: 2h 22m')
    await wrapper.setProps({ item: { ...props.item, completed_at: null } })
    expect(wrapper.get('.active-execution-line__elapsed').text()).toBe('N/A')
    wrapper.unmount()
  })
  it('shows recording only with a fresh feed and a real running execution', async () => {
    const feed = ref<UsageFeedState>({ status: 'connecting', receivedAt: null })
    const wrapper = mount(ActiveExecutionLine, { props: { execution }, global: { provide: { [usageFeedKey as symbol]: feed } } })
    expect(wrapper.get('.usage-live-indicator').text()).toBe('Connecting')
    feed.value = { status: 'live', receivedAt: Date.now() }
    await wrapper.vm.$nextTick()
    expect(wrapper.get('.usage-live-indicator--live').text()).toBe('Live')
    feed.value = { ...feed.value, status: 'reconnecting' }
    await wrapper.vm.$nextTick()
    expect(wrapper.get('.usage-live-indicator').text()).toBe('Reconnecting')
    expect(wrapper.find('.usage-live-indicator--live').exists()).toBe(false)
    await wrapper.setProps({ execution: null, lastExecution: execution })
    expect(wrapper.find('.usage-live-indicator').exists()).toBe(false)
    await wrapper.setProps({ lastExecution: null, owner: 'Codex Main' })
    expect(wrapper.find('.usage-live-indicator').exists()).toBe(false)
    wrapper.unmount()
  })
  it('renders actual host identity, explicitly retains Last and never invents an occurrence for assignment', async () => {
    const wrapper = mount(ActiveExecutionLine, { props: { execution, owner: 'Codex' } })
    expect(wrapper.get('img').attributes('src')).toBe('/images/openai.webp')
    await wrapper.setProps({ execution: null, lastExecution: { ...execution, agent: 'sdlc_delivery_reviewer', host_key: 'claude-code' } })
    expect(wrapper.get('img').attributes('src')).toBe('/images/claude-code.webp')
    expect(wrapper.get('[data-last-execution]').text()).toContain('Last: Delivery reviewer')
    expect(wrapper.find('[data-active-execution]').exists()).toBe(false)
    expect(wrapper.find('time').exists()).toBe(false)
    expect(wrapper.attributes('title')).toContain(`Started ${new Date(execution.started_at).toLocaleString()}`)
    await wrapper.setProps({ lastExecution: null })
    expect(wrapper.text()).toBe('Assigned: Codex')
    expect(wrapper.text()).not.toContain('gpt-6.1-sol')
    expect(wrapper.find('time').exists()).toBe(false)
  })
  it('switches identity across delivery/review/repair without resetting item elapsed', async () => {
    vi.useFakeTimers().setSystemTime(new Date('2026-11-08T01:22:00Z'))
    const item = { status: 'in-progress' as const, started_at: execution.started_at, completed_at: null }
    const wrapper = mount(ActiveExecutionLine, { props: { item, execution, focusable: true } })
    expect(wrapper.get('.active-execution-line__elapsed').text()).toBe('14m')
    expect(wrapper.attributes('title')).toContain(execution.agent)
    expect(wrapper.attributes('aria-label')).toContain(new Date(execution.started_at).toLocaleString())
    expect(wrapper.attributes('tabindex')).toBe('0')
    expect(wrapper.text()).not.toContain('call-1')
    for (const [index, agent, model] of [[2, 'Reviewer', 'gpt-6-astra'], [3, 'Implementer', 'gpt-6.1-sol']] as const) {
      const next = { ...execution, invocation_id: `call-${index}`, agent, model, started_at: `2026-11-08T01:${10 + index}:00Z` }
      await wrapper.setProps({ execution: next })
      expect(wrapper.text()).toContain(agent)
      expect(wrapper.text()).toContain(model)
      expect(wrapper.get('.active-execution-line__elapsed').text()).toBe('14m')
      expect(wrapper.text()).not.toContain('Project delivery')
    }
    await wrapper.setProps({ execution: null })
    expect(wrapper.find('[data-active-execution]').exists()).toBe(false)
    expect(wrapper.get('.active-execution-line__elapsed').text()).toBe('14m')
    wrapper.unmount()
  })
})
