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

export type StackOsRouteQuery = Record<string, unknown>

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
        { key: 'operations', label: 'Operations', to: `${base}/operations` },
        { key: 'action-calls', label: 'Action Calls', to: `${base}/action-calls` },
        { key: 'agent-requests', label: 'Agent Requests', to: `${base}/agent-requests` },
        { key: 'agent-presets', label: 'Agent Presets', to: `${base}/agent-presets` },
        { key: 'tasks', label: 'Tasks', to: `${base}/tasks` },
        {
          key: 'workflow-templates',
          label: 'Workflow Templates',
          to: `${base}/workflow-templates`,
        },
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
      { key: 'setup', label: 'Setup Status', to: `${base}/setup` },
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

export function isStackOsNavItemActive(
  item: Pick<StackOsNavItem, 'to' | 'matchPrefix'>,
  currentPath: string,
  currentQuery: StackOsRouteQuery = {},
): boolean {
  const [targetPath, targetSearch = ''] = item.to.split('?')
  const pathActive = item.matchPrefix
    ? currentPath === targetPath || currentPath.startsWith(`${targetPath}/`)
    : currentPath === targetPath
  if (!pathActive) return false

  const targetParams = new URLSearchParams(targetSearch)
  const targetKeys = Array.from(new Set(Array.from(targetParams.keys())))
  if (targetKeys.length > 0) {
    return targetKeys.every((key) => queryValueMatches(currentQuery[key], targetParams.getAll(key)))
  }

  return currentQuery.plugin_slug === undefined || currentQuery.plugin_slug === null
}

function isPluginNav(value: unknown): value is PluginNavContribution {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function queryValueMatches(current: unknown, expected: string[]): boolean {
  if (expected.length === 0) return current === undefined || current === null
  if (Array.isArray(current)) {
    return expected.every((value) => current.map(String).includes(value))
  }
  return expected.includes(String(current ?? ''))
}
