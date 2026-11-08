import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ActiveExecutionLine from './ActiveExecutionLine.vue'

const execution = { invocation_id: 'call-1', agent: 'Project_delivery_agent_with_a_long_name', host_key: 'codex', model: 'gpt-6.1-sol', started_at: '2026-11-08T01:08:00Z', ticket_key: 'child' }

describe('ActiveExecutionLine', () => {
  it('switches identity and current-call start across delivery/review/repair and removes inactive calls', async () => {
    const wrapper = mount(ActiveExecutionLine, { props: { execution, focusable: true } })
    expect(wrapper.get('time').text()).toContain(new Date(execution.started_at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }))
    expect(wrapper.attributes('title')).toContain(execution.agent)
    expect(wrapper.attributes('aria-label')).toContain(new Date(execution.started_at).toLocaleString())
    expect(wrapper.attributes('tabindex')).toBe('0')
    expect(wrapper.text()).not.toContain('call-1')
    for (const [index, agent, model] of [[2, 'Reviewer', 'gpt-6-astra'], [3, 'Implementer', 'gpt-6.1-sol']] as const) {
      const next = { ...execution, invocation_id: `call-${index}`, agent, model, started_at: `2026-11-08T01:${10 + index}:00Z` }
      await wrapper.setProps({ execution: next })
      expect(wrapper.text()).toContain(agent)
      expect(wrapper.text()).toContain(model)
      expect(wrapper.get('time').attributes('datetime')).toBe(next.started_at)
      expect(wrapper.text()).not.toContain('Project delivery')
    }
    await wrapper.setProps({ execution: null })
    expect(wrapper.find('[data-active-execution]').exists()).toBe(false)
  })
})
