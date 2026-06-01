import { describe, expect, it } from 'vitest'

import type { SchemaPluginOut } from '@/api'
import {
  coreNavSections,
  isStackOsNavItemActive,
  pluginContributionSections,
  projectNavSections,
  setupNavSection,
} from './nav'

describe('StackOS nav contributions', () => {
  it('orders generic project nav by operational semantics', () => {
    const core = coreNavSections(7)
    const labels = core.map((section) => section.label)

    expect(labels).toEqual(['Work', 'Workflows', 'Knowledge', 'Integrations', 'System'])
    expect(core[0].items.map((item) => item.label)).toEqual(['Overview', 'Tasks', 'Runs'])
    expect(core[1].items.map((item) => item.label)).toEqual([
      'Workflow Library',
      'Agent Presets',
      'Agent Requests',
    ])
    expect(core[2].items.map((item) => item.label)).toEqual(['Project Data'])
    expect(core[2].items.map((item) => item.to)).not.toContain('/projects/7/resources')
    expect(core.flatMap((section) => section.items.map((item) => item.to))).toContain(
      '/projects/7/action-calls',
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

  it('exposes project setup status before setup support pages', () => {
    const section = setupNavSection(12)

    expect(section.items.map((item) => item.to)).toEqual([
      '/projects/12/setup',
      '/projects/12/schedules',
      '/projects/12/cost-budget',
    ])
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
                label: 'Data',
                to: 'resources?plugin_slug=seo',
                matchPrefix: true,
              },
              {
                key: 'seo.templates',
                label: 'Workflows',
                to: 'workflow-templates?plugin_slug=seo',
              },
            ],
          },
        },
      },
    } as SchemaPluginOut

    const sections = pluginContributionSections(7, [plugin])
    expect(sections[0].label).toBe('SEO')
    expect(sections[0].items.map((item) => item.label)).toEqual(['Data', 'Workflows'])
    expect(sections[0].items.map((item) => item.to)).toEqual([
      '/projects/7/resources?plugin_slug=seo',
      '/projects/7/workflow-templates?plugin_slug=seo',
    ])

    const disabled = { ...plugin, enabled_for_project: false } as SchemaPluginOut
    expect(pluginContributionSections(7, [disabled])).toEqual([])
  })

  it('keeps project nav first and orders plugin tools with engineering first', () => {
    const engineering = pluginFixture('engineering', 'Engineering')
    const communications = pluginFixture('communications', 'Communications')
    const seo = pluginFixture('seo', 'SEO')

    const sections = projectNavSections(7, [seo, communications, engineering])

    expect(sections[0].label).toBe('Work')
    expect(sections[1].label).toBe('Workflows')
    expect(sections[2].label).toBe('Engineering')
    expect(sections[3].label).toBe('Communications')
    expect(sections[2].items.map((item) => item.to)).toEqual([
      '/projects/7/resources?plugin_slug=engineering',
      '/projects/7/workflow-templates?plugin_slug=engineering',
    ])
    expect(sections.map((section) => section.label)).toContain('SEO')
  })

  it('uses manifest display order for plugin tools before falling back to slug defaults', () => {
    const customEarly = pluginFixture('custom-early', 'Custom Early', 5)
    const engineering = pluginFixture('engineering', 'Engineering')
    const communications = pluginFixture('communications', 'Communications')

    const sections = pluginContributionSections(7, [communications, engineering, customEarly])

    expect(sections.map((section) => section.label)).toEqual([
      'Custom Early',
      'Engineering',
      'Communications',
    ])
  })

  it('keeps plugin-scoped nav active state separate from generic pages', () => {
    const genericResources = { to: '/projects/7/resources' }
    const seoResources = {
      to: '/projects/7/resources?plugin_slug=seo',
      matchPrefix: true,
    }
    const genericTemplates = { to: '/projects/7/workflow-templates' }
    const seoTemplates = { to: '/projects/7/workflow-templates?plugin_slug=seo' }

    expect(isStackOsNavItemActive(genericResources, '/projects/7/resources', {})).toBe(true)
    expect(
      isStackOsNavItemActive(genericResources, '/projects/7/resources', { plugin_slug: 'seo' }),
    ).toBe(false)
    expect(
      isStackOsNavItemActive(seoResources, '/projects/7/resources', { plugin_slug: 'seo' }),
    ).toBe(true)
    expect(
      isStackOsNavItemActive(seoResources, '/projects/7/resources', { plugin_slug: 'core' }),
    ).toBe(false)
    expect(
      isStackOsNavItemActive(genericTemplates, '/projects/7/workflow-templates', {
        plugin_slug: 'seo',
      }),
    ).toBe(false)
    expect(
      isStackOsNavItemActive(seoTemplates, '/projects/7/workflow-templates', {
        plugin_slug: 'seo',
      }),
    ).toBe(true)
  })
})

function pluginFixture(slug: string, section: string, displayOrder?: number): SchemaPluginOut {
  return {
    id: slug === 'engineering' ? 1 : 2,
    slug,
    name: section,
    version: '0.1.0',
    description: '',
    source: 'builtin',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    enabled_for_project: true,
    manifest_json: {
      ...(displayOrder === undefined ? {} : { display_order: displayOrder }),
      ui: {
        nav: {
          section,
          items: [
            {
              key: `${slug}.resources`,
              label: 'Data',
              to: `resources?plugin_slug=${slug}`,
              matchPrefix: true,
            },
            {
              key: `${slug}.templates`,
              label: 'Workflows',
              to: `workflow-templates?plugin_slug=${slug}`,
            },
          ],
        },
      },
    },
  } as SchemaPluginOut
}
