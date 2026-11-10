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
  /** Optional presentational section icon. */
  icon?: string
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

export function coreNavSections(projectId: number): StackOsNavSection[] {
  const base = `/projects/${projectId}`
  return [
    {
      key: 'project',
      label: '',
      items: [
        { key: 'home', label: 'Home', to: base, icon: 'home' },
        { key: 'tasks', label: 'Work', to: `${base}/tasks`, matchPrefix: true, icon: 'tasks' },
        { key: 'schedules', label: 'Automations', to: `${base}/schedules`, icon: 'bolt' },
        { key: 'library', label: 'Library', to: `${base}/library`, icon: 'folder' },
      ],
    },
    {
      key: 'setup',
      label: 'Setup',
      items: [
        { key: 'connections', label: 'Connections', to: `${base}/connections`, icon: 'link' },
        { key: 'cost-budget', label: 'Spend', to: `${base}/cost-budget`, icon: 'database' },
        {
          key: 'agents',
          label: 'Agents',
          to: `${base}/agents`,
          icon: 'user',
        },
        { key: 'plugins', label: 'Plugins', to: `${base}/plugins`, icon: 'puzzle' },
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
    agency: 'users',
    finance: 'database',
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
  return [...coreNavSections(projectId), ...pluginContributionSections(projectId, plugins)]
}

export function pluginContributionSections(
  projectId: number,
  plugins: SchemaPluginOut[],
): StackOsNavSection[] {
  const base = `/projects/${projectId}`
  const items: StackOsNavItem[] = []
  for (const plugin of [...plugins].sort(comparePlugins)) {
    if (plugin.enabled_for_project === false) continue
    const ui = isRecord(plugin.manifest_json?.ui) ? plugin.manifest_json.ui : null
    const nav = isRecord(ui?.nav) ? ui.nav : null
    if (!nav && !isRecord(ui?.page)) continue
    // Domain identity owns the destination. Normalize older Data/Workflows
    // contributions here too, including Agency and Support.
    const label = plugin.slug === 'finance' ? 'Finance'
      : typeof nav?.section === 'string' ? nav.section : plugin.name
    items.push({
      key: `plugin-${plugin.slug}`,
      label,
      to: plugin.slug === 'finance' ? `${base}/finance` : `${base}/plugins/${encodeURIComponent(plugin.slug)}`,
      matchPrefix: true,
      icon: pluginSectionIcon(plugin.slug),
    })
  }
  return items.length ? [{ key: 'domains', label: 'Plugins', items }] : []
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

  return true
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
