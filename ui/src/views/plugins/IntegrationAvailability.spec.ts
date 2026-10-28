import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import IntegrationAvailability from './IntegrationAvailability.vue'

const connectionsHref = '/projects/7/connections?section=services'
const item = {
  plugin_slug: 'utils', provider_key: 'firecrawl', state: 'not_connected',
  executable_action_count: 2, total_action_count: 5, hidden_action_count: 1,
}

describe('integration availability presentation', () => {
  it.each([
    ['provider_disabled', 'Provider disabled'],
    ['connected_blocked', 'Connected · actions blocked'],
    ['connected', 'Connected'],
    ['repair_required', 'Account repair required'],
    ['partially_available', 'Partially available'],
    ['not_connected', 'Not connected'],
    ['ready_no_connection_required', 'Ready · no connection required'],
    ['available', 'Available'],
  ])('preserves the canonical %s state and executable/hidden counts', (state, label) => {
    const wrapper = mount(IntegrationAvailability, {
      props: { availability: { ...item, state }, loading: false, connectionsHref },
    })
    expect(wrapper.text()).toContain(label)
    expect(wrapper.text()).toContain('2 of 5 actions executable')
    expect(wrapper.text()).toContain('1 hidden from agent discovery')
    expect(wrapper.find('a').exists()).toBe(state === 'repair_required')
  })

  it('routes actionable repair to existing Connections without opening Attach Account', () => {
    const wrapper = mount(IntegrationAvailability, {
      props: {
        availability: { ...item, state: 'repair_required', next_action: {
          tool: 'auth.test', reason: 'Review the Account permissions.',
          ui_url: '/projects/7/connections?provider_key=firecrawl',
        } }, loading: false, connectionsHref,
      },
    })
    expect(wrapper.text()).toContain('Review the Account permissions.')
    expect(wrapper.get('a').attributes('href')).toBe(connectionsHref)
  })

  it('keeps missing data and pending data explicit', async () => {
    const wrapper = mount(IntegrationAvailability, { props: { loading: true, connectionsHref } })
    expect(wrapper.text()).toBe('Checking availability…')
    await wrapper.setProps({ loading: false })
    expect(wrapper.text()).toBe('Availability unavailable')
  })
})
