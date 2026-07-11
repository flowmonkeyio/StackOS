import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import PluginDirectoryCard from './PluginDirectoryCard.vue'
import type { PluginDirectoryItem } from './viewModel'

describe('PluginDirectoryCard', () => {
  it('keeps description and provider content in stable card tracks', () => {
    const wrapper = mount(PluginDirectoryCard, { props: { item: directoryItem(5) } })

    expect(wrapper.get('.plugin-directory-card__description').classes()).toContain('line-clamp-3')
    expect(wrapper.get('.plugin-directory-card__providers').text()).toContain('5 providers')
    expect(wrapper.get('.plugin-directory-card__providers').text()).toContain('+1 more')
    expect(wrapper.findAll('.plugin-directory-card__providers li')).toHaveLength(4)
  })

  it('reserves the provider track when a plugin has no providers', () => {
    const wrapper = mount(PluginDirectoryCard, { props: { item: directoryItem(0) } })

    expect(wrapper.get('.plugin-directory-card__providers').text()).toContain(
      'No provider setup required.',
    )
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
    searchText: '',
  } as unknown as PluginDirectoryItem
}
