import type { SchemaPluginOut } from '@/api'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export type DomainSection = 'workflows' | 'saved' | 'files'
export function domainPage(plugin: SchemaPluginOut | undefined) {
  const ui = plugin?.manifest_json?.ui
  const page = isRecord(ui) && isRecord(ui.page) ? ui.page : null
  const sections = Array.isArray(page?.sections) ? page.sections.filter((section): section is DomainSection => ['workflows', 'saved', 'files'].includes(section)) : []
  return { sections: [...new Set(sections)], description: typeof page?.description === 'string' ? page.description : plugin?.description ?? '' }
}
export function domainLocation(projectId: number, pluginSlug: string) {
  return `/projects/${projectId}/plugins/${encodeURIComponent(pluginSlug)}`
}
export function workflowLocation(projectId: number, pluginSlug: string, key: string) {
  return `${domainLocation(projectId, pluginSlug)}/workflows/${encodeURIComponent(key)}`
}
