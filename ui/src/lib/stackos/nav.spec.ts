import { describe, expect, it } from 'vitest'

import type { SchemaPluginOut } from '@/api'
import { coreNavSections, pluginContributionSections } from './nav'

describe('StackOS nav contributions', () => {
  it('keeps generic core nav focused on StackOS primitives', () => {
    const core = coreNavSections(7)

    expect(core.flatMap((section) => section.items.map((item) => item.to))).toContain(
      '/projects/7/workflow-templates',
    )
    expect(core.flatMap((section) => section.items.map((item) => item.to))).toContain(
      '/projects/7/resources',
    )
  })

  it('loads plugin nav contributions from sanitized manifest UI metadata', () => {
    const plugin = {
      id: 1,
      slug: 'media-buying',
      name: 'Media Buying',
      version: '0.1.0',
      description: '',
      source: 'builtin',
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      enabled_for_project: true,
      manifest_json: {
        ui: {
          nav: {
            section: 'Media Buying',
            items: [{ key: 'campaigns', label: 'Campaigns', to: 'resources' }],
          },
        },
      },
    } as SchemaPluginOut

    const sections = pluginContributionSections(9, [plugin])

    expect(sections).toHaveLength(1)
    expect(sections[0].label).toBe('Media Buying')
    expect(sections[0].items[0]).toMatchObject({
      key: 'campaigns',
      label: 'Campaigns',
      to: '/projects/9/resources',
    })
  })

  it('loads SEO nav from the plugin manifest contribution only when enabled', () => {
    const plugin = {
      id: 2,
      slug: 'seo',
      name: 'SEO',
      version: '0.1.0',
      description: '',
      source: 'builtin',
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      enabled_for_project: true,
      manifest_json: {
        ui: {
          nav: {
            section: 'SEO',
            items: [
              {
                key: 'seo.resources',
                label: 'SEO Resources',
                to: 'resources?plugin_slug=seo',
                matchPrefix: true,
              },
              {
                key: 'seo.templates',
                label: 'SEO Templates',
                to: 'workflow-templates?plugin_slug=seo',
              },
            ],
          },
        },
      },
    } as SchemaPluginOut

    const sections = pluginContributionSections(7, [plugin])
    expect(sections[0].label).toBe('SEO')
    expect(sections[0].items.map((item) => item.to)).toEqual([
      '/projects/7/resources?plugin_slug=seo',
      '/projects/7/workflow-templates?plugin_slug=seo',
    ])

    const disabled = { ...plugin, enabled_for_project: false } as SchemaPluginOut
    expect(pluginContributionSections(7, [disabled])).toEqual([])
  })
})
