import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import PluginDirectoryCard from './PluginDirectoryCard.vue'
import type { PluginDirectoryItem } from './viewModel'

describe('PluginDirectoryCard', () => {
  const connectionsHref = '/projects/1/connections?section=services'

  it('previews services and lets the operator inspect the complete catalog details', async () => {
    const wrapper = mount(PluginDirectoryCard, { props: { item: directoryItem(5), connectionsHref } })

    expect(wrapper.get('ul[aria-label="SEO services"]').text()).toContain('+1 more')
    expect(wrapper.findAll('ul[aria-label="SEO services"] li')).toHaveLength(5)
    expect(wrapper.get('details').attributes('open')).toBeUndefined()
    expect(wrapper.get('ul[aria-label="Service availability"]').text()).toContain('Provider 5')
    await wrapper.get('button').trigger('click')
    expect(wrapper.emitted('showProviders')).toEqual([[]])
  })

  it('omits service navigation when a plugin supplies no services', () => {
    const wrapper = mount(PluginDirectoryCard, { props: { item: directoryItem(0), connectionsHref } })

    expect(wrapper.text()).toContain('No services required.')
    expect(wrapper.find('button').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Ready')
  })

  it.each([true, false])('keeps enablement %s independent of each real service state', (enabled) => {
    const item = directoryItem(5)
    item.plugin.enabled_for_project = enabled
    item.integrations = ['connected', 'not_connected', 'partially_available', 'repair_required', 'unknown'].map((state, index) => ({
      plugin_slug: 'seo', provider_key: `provider-${index + 1}`, state,
      total_action_count: 5, executable_action_count: 0, hidden_action_count: 0,
    }))
    const wrapper = mount(PluginDirectoryCard, { props: { item, connectionsHref } })
    expect(wrapper.text()).toContain(enabled ? 'Enabled' : 'Available')
    const states = wrapper.get('ul[aria-label="Service availability"]').text()
    for (const label of ['Connected', 'Not connected', 'Partially available', 'Account repair required', 'Unknown']) {
      expect(states).toContain(label)
    }
    expect(wrapper.text()).not.toContain('Ready')
    const visibleRepair = wrapper.findAll('a').filter((link) => !link.element.closest('details'))
    expect(visibleRepair).toHaveLength(1)
    expect(visibleRepair[0]?.attributes('href')).toBe(connectionsHref)
    expect(wrapper.findAll('.ui-badge').filter((badge) => !badge.element.closest('details')).map((badge) => badge.text())).toContain('Account repair required')
  })
})

function directoryItem(providerCount: number): PluginDirectoryItem {
  return {
    plugin: {
      slug: 'seo',
      name: 'SEO',
      description:
        'A deliberately long plugin description that wraps across several lines while the provider region remains aligned with adjacent cards.',
      version: '1.0.0',
      source: 'builtin',
      enabled_for_project: true,
    },
    providers: Array.from({ length: providerCount }, (_, index) => ({
      id: index + 1,
      key: `provider-${index + 1}`,
      name: `Provider ${index + 1}`,
      plugin_slug: 'seo',
    })),
    actions: [],
    capabilities: [],
    integrations: [],
    searchText: '',
  } as unknown as PluginDirectoryItem
}
