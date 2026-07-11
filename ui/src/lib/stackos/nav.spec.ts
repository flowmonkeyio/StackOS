import { describe, expect, it } from 'vitest'

import type { SchemaPluginOut } from '@/api'
import {
  coreNavSections,
  isStackOsNavItemActive,
  pluginContributionSections,
  projectNavSections,
} from './nav'

describe('StackOS nav contributions', () => {
  it('leads with the five operator lanes and groups secondary inspection by job', () => {
    const core = coreNavSections(7)
    const labels = core.map((section) => section.label)

    expect(labels).toEqual(['Operate', 'Setup tools', 'Execution', 'Catalog', 'Data & evidence'])
    expect(core[0].items.map((item) => item.label)).toEqual([
      'Home',
      'Attention',
      'Work',
      'Activity',
      'Setup',
    ])
    // Home is the project index; Work is the tracker route.
    expect(core[0].items[0].to).toBe('/projects/7')
    expect(core[0].items.find((item) => item.label === 'Work')?.to).toBe('/projects/7/tasks')
    expect(core[1].items.map((item) => item.label)).toEqual([
      'Connections',
      'Automation',
      'Spend',
      'Plugins',
    ])
    // Execution, catalog, and evidence remain secondary inspection groups.
    expect(core[2].items.map((item) => item.to)).toContain('/projects/7/action-calls')
    expect(core[3].items.map((item) => item.to)).toContain('/projects/7/operations')
    expect(core[4].items.map((item) => item.to)).toContain('/projects/7/resources')
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

  it('keeps setup-owner surfaces in the Setup tools group', () => {
    const core = coreNavSections(12)
    const configure = core.find((section) => section.label === 'Setup tools')

    expect(configure?.items.map((item) => item.to)).toEqual([
      '/projects/12/connections',
      '/projects/12/schedules',
      '/projects/12/cost-budget',
      '/projects/12/plugins',
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

    expect(sections[0].label).toBe('Operate')
    expect(sections[1].label).toBe('Setup tools')
    expect(sections[2].label).toBe('Execution')
    expect(sections[3].label).toBe('Catalog')
    expect(sections[4].label).toBe('Data & evidence')
    expect(sections[5].label).toBe('Engineering')
    expect(sections[6].label).toBe('Communications')
    expect(sections[5].items.map((item) => item.to)).toEqual([
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
