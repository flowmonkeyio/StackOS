import { describe, expect, it } from 'vitest'

import type { SchemaPluginOut } from '@/api'
import {
  compatibilityNavSection,
  coreNavSections,
  pluginContributionSections,
} from './nav'

describe('StackOS nav contributions', () => {
  it('keeps generic core nav separate from compatibility nav', () => {
    const core = coreNavSections(7)
    const compatibility = compatibilityNavSection(7)

    expect(core.flatMap((section) => section.items.map((item) => item.to))).toContain(
      '/projects/7/workflow-templates',
    )
    expect(core.flatMap((section) => section.items.map((item) => item.to))).toContain(
      '/projects/7/resources',
    )
    expect(compatibility.items.map((item) => item.to)).toContain('/projects/7/articles')
    expect(compatibility.items.map((item) => item.to)).toContain('/projects/7/procedures')
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
})
