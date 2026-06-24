import type { SchemaPluginOut } from '@/api'

export interface StackOsNavItem {
  key: string
  label: string
  to: string
  description?: string
  matchPrefix?: boolean
  /** Icon registry name (components/ui/icons.ts). Optional, presentational. */
  icon?: string
}

export interface StackOsNavSection {
  key: string
  label: string
  items: StackOsNavItem[]
  /** Icon registry name for collapsible group headers. Optional, presentational. */
  icon?: string
  /** Plugin-contributed sections render as collapsible groups. */
  collapsible?: boolean
}

export type StackOsRouteQuery = Record<string, unknown>

const PLUGIN_ORDER: Record<string, number> = {
  engineering: 10,
  communications: 20,
  gtm: 30,
  'media-buying': 40,
  publishing: 50,
  seo: 60,
  core: 900,
  utils: 910,
}

const PLUGIN_LABELS: Record<string, string> = {
  engineering: 'Engineering',
  communications: 'Communications',
  gtm: 'GTM',
  'media-buying': 'Media Buying',
  publishing: 'Publishing',
  seo: 'SEO',
  core: 'Core',
  utils: 'Utilities',
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
      key: 'operate',
      label: 'Operate',
      items: [
        { key: 'home', label: 'Home', to: base, icon: 'home' },
        { key: 'inbox', label: 'Inbox', to: `${base}/inbox`, icon: 'inbox' },
        { key: 'tasks', label: 'Work', to: `${base}/tasks`, matchPrefix: true, icon: 'tasks' },
        { key: 'activity', label: 'Activity', to: `${base}/activity`, icon: 'list' },
        { key: 'setup', label: 'Setup', to: `${base}/setup`, icon: 'settings' },
      ],
    },
    {
      key: 'configure',
      label: 'Configure',
      items: [
        { key: 'connections', label: 'Connections', to: `${base}/connections`, icon: 'link' },
        { key: 'schedules', label: 'Automation', to: `${base}/schedules`, icon: 'calendar' },
        { key: 'cost-budget', label: 'Spend', to: `${base}/cost-budget`, icon: 'banknotes' },
        { key: 'plugins', label: 'Plugins', to: `${base}/plugins`, icon: 'puzzle' },
      ],
    },
    {
      key: 'developer',
      label: 'Developer',
      items: [
        { key: 'runs', label: 'Runs', to: `${base}/runs`, matchPrefix: true, icon: 'runs' },
        {
          key: 'workflow-templates',
          label: 'Workflow Library',
          to: `${base}/workflow-templates`,
          icon: 'library',
        },
        { key: 'agent-presets', label: 'Agent Presets', to: `${base}/agent-presets`, icon: 'users' },
        { key: 'agent-requests', label: 'Agent Requests', to: `${base}/agent-requests`, icon: 'queue-list' },
        { key: 'capabilities', label: 'Capabilities', to: `${base}/capabilities`, icon: 'chip' },
        { key: 'operations', label: 'Operations', to: `${base}/operations`, icon: 'terminal' },
        { key: 'action-calls', label: 'Action Calls', to: `${base}/action-calls`, icon: 'bolt' },
        { key: 'resources', label: 'Resources', to: `${base}/resources`, icon: 'folder' },
        { key: 'project-data', label: 'Project Data', to: `${base}/data`, icon: 'database' },
      ],
    },
  ]
}

/** Decorative icon for a plugin-contributed nav section, by slug. */
export function pluginSectionIcon(slug: string): string {
  const icons: Record<string, string> = {
    engineering: 'code-bracket',
    communications: 'chat',
    gtm: 'megaphone',
    'media-buying': 'chart-bar',
    publishing: 'newspaper',
    seo: 'search',
    support: 'lifebuoy',
    trackbooth: 'sparkles',
    core: 'cube',
    utils: 'wrench',
  }
  return icons[slug] ?? 'puzzle'
}

export function projectNavSections(
  projectId: number,
  plugins: SchemaPluginOut[],
): StackOsNavSection[] {
  const [operate, configure, developer] = coreNavSections(projectId)
  const pluginSections = pluginContributionSections(projectId, plugins)
  // Operate + Configure lead; plugin lanes sit between config and the demoted
  // Developer (audit/registry) group.
  return [operate, configure, ...pluginSections, developer].filter(
    (section): section is StackOsNavSection => Boolean(section?.items.length),
  )
}

export function pluginContributionSections(
  projectId: number,
  plugins: SchemaPluginOut[],
): StackOsNavSection[] {
  const base = `/projects/${projectId}`
  const sections: StackOsNavSection[] = []
  for (const plugin of [...plugins].sort(comparePlugins)) {
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
        icon: pluginSectionIcon(plugin.slug),
        collapsible: true,
      })
    }
  }
  return sections
}

function comparePlugins(a: SchemaPluginOut, b: SchemaPluginOut): number {
  return (
    stackOsPluginDisplayOrder(a.slug, a.manifest_json) -
      stackOsPluginDisplayOrder(b.slug, b.manifest_json) ||
    a.name.localeCompare(b.name)
  )
}

export function stackOsPluginDisplayOrder(
  slug: string,
  manifestJson?: { display_order?: unknown } | null,
): number {
  const rawOrder = manifestJson?.display_order
  if (typeof rawOrder === 'number') return rawOrder
  if (typeof rawOrder === 'string' && /^\d+$/.test(rawOrder)) return Number.parseInt(rawOrder, 10)
  return PLUGIN_ORDER[slug] ?? 500
}

export function stackOsPluginLabel(slug?: string | null): string {
  if (!slug) return 'Unknown'
  return PLUGIN_LABELS[slug] ?? slug.replace(/-/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase())
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
