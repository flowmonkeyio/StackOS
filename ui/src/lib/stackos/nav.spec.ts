// @vitest-environment node
import { describe, expect, it } from 'vitest'
import type { SchemaPluginOut } from '@/api'
import { coreNavSections, isStackOsNavItemActive, pluginContributionSections, projectNavSections } from './nav'

function plugin(slug: string, order = 10, enabled = true): SchemaPluginOut {
  return { id: 1, created_at: '', updated_at: '', description: '', source: 'builtin', version: '0.1.0', slug, name: slug === 'finance' ? 'Finance Operations' : slug, enabled_for_project: enabled,
    manifest_json: { display_order: order, ui: { nav: { section: slug, items: [{ label: 'Data', to: `resources?plugin_slug=${slug}` }, { label: 'Workflows', to: `workflow-templates?plugin_slug=${slug}` }] } } },
  } as SchemaPluginOut
}

describe('project navigation', () => {
  it('connects the primary and Setup destinations without category pages', () => {
    const sections = coreNavSections(7)
    expect(sections.map(section => section.items.map(item => [item.label, item.to]))).toEqual([
      [['Home', '/projects/7'], ['Work', '/projects/7/tasks'], ['Automations', '/projects/7/schedules'], ['Library', '/projects/7/library']],
      [['Connections', '/projects/7/connections'], ['Spend', '/projects/7/cost-budget'], ['Agents', '/projects/7/agents'], ['Plugins', '/projects/7/plugins']],
    ])
  })
  it('normalizes enabled legacy contributions to direct domains, preserving Finance and display order', () => {
    const sections = projectNavSections(7, [plugin('agency', 19), plugin('support', 15), plugin('finance', 18), plugin('engineering'), plugin('seo', 60, false)])
    expect(sections.map(section => section.key)).toEqual(['project', 'setup', 'domains'])
    expect(sections[2]!.items.map(item => item.to)).toEqual([
      '/projects/7/plugins/engineering', '/projects/7/plugins/support', '/projects/7/finance', '/projects/7/plugins/agency',
    ])
    expect(sections[2]!.items[2]!.label).toBe('Finance')
    expect(sections[2]!.items).toHaveLength(4)
  })
  it('does not create domains for provider-only packages without a declared page or navigation', () => {
    expect(pluginContributionSections(7, [{ ...plugin('transport'), manifest_json: {} }])).toEqual([])
  })
  it('keeps domain selection on workflow details and Library selection on filtered records', () => {
    expect(isStackOsNavItemActive({ to: '/projects/7/plugins/seo', matchPrefix: true }, '/projects/7/plugins/seo/workflows/seo.audit')).toBe(true)
    expect(isStackOsNavItemActive({ to: '/projects/7/plugins' }, '/projects/7/plugins/seo')).toBe(false)
    expect(isStackOsNavItemActive({ to: '/projects/7/library' }, '/projects/7/library', { plugin: 'seo', item: 'resource_record:42' })).toBe(true)
    expect(isStackOsNavItemActive({ to: '/projects/7/plugins/seo', matchPrefix: true }, '/projects/7/plugins/seo-other')).toBe(false)
  })
})
