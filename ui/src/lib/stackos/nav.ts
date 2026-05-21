import type { SchemaPluginOut } from '@/api'

export interface StackOsNavItem {
  key: string
  label: string
  to: string
  description?: string
  matchPrefix?: boolean
}

export interface StackOsNavSection {
  key: string
  label: string
  items: StackOsNavItem[]
}

interface PluginNavContribution {
  section?: string
  items?: Array<{
    key?: string
    label?: string
    to?: string
    description?: string
    matchPrefix?: boolean
  }>
}

export function coreNavSections(projectId: number): StackOsNavSection[] {
  const base = `/projects/${projectId}`
  return [
    {
      key: 'stackos-core',
      label: 'StackOS',
      items: [
        { key: 'overview', label: 'Overview', to: `${base}/overview` },
        { key: 'plugins', label: 'Plugins', to: `${base}/plugins` },
        { key: 'capabilities', label: 'Capabilities', to: `${base}/capabilities` },
        { key: 'connections', label: 'Connections', to: `${base}/connections` },
        { key: 'workflow-templates', label: 'Workflow Templates', to: `${base}/workflow-templates` },
        { key: 'runs', label: 'Runs', to: `${base}/runs`, matchPrefix: true },
      ],
    },
    {
      key: 'project-data',
      label: 'Project Data',
      items: [
        { key: 'project-data', label: 'Data', to: `${base}/data` },
        { key: 'resources', label: 'Resources', to: `${base}/resources` },
      ],
    },
  ]
}

export function setupNavSection(projectId: number): StackOsNavSection {
  const base = `/projects/${projectId}`
  return {
    key: 'project-setup',
    label: 'Project Setup',
    items: [
      { key: 'schedules', label: 'Schedules', to: `${base}/schedules` },
      { key: 'cost-budget', label: 'Cost & Budget', to: `${base}/cost-budget` },
    ],
  }
}

export function pluginContributionSections(
  projectId: number,
  plugins: SchemaPluginOut[],
): StackOsNavSection[] {
  const base = `/projects/${projectId}`
  const sections: StackOsNavSection[] = []
  for (const plugin of plugins) {
    if (plugin.enabled_for_project === false) continue
    const ui = isRecord(plugin.manifest_json?.ui) ? plugin.manifest_json.ui : null
    const nav = isPluginNav(ui?.nav) ? ui.nav : null
    if (!nav) continue
    const items: StackOsNavItem[] = []
    for (const item of nav.items ?? []) {
      if (item.label && item.to) {
        const to = item.to.startsWith('/') ? item.to : `${base}/${item.to.replace(/^\/+/, '')}`
        items.push({
          key: item.key ?? `${plugin.slug}.${item.label}`,
          label: item.label,
          to,
          description: item.description,
          matchPrefix: item.matchPrefix,
        })
      }
    }
    if (items.length > 0) {
      sections.push({
        key: `plugin-${plugin.slug}`,
        label: nav.section ?? plugin.name,
        items,
      })
    }
  }
  return sections
}

function isPluginNav(value: unknown): value is PluginNavContribution {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
