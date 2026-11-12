import type { SchemaPluginOut } from '@/api'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export type DomainSection = 'workflows' | 'saved' | 'files'

/** Shared declaration used by the sidebar and the generic domain renderer. */
export function domainPage(plugin: SchemaPluginOut | undefined) {
  const ui = plugin?.manifest_json?.ui
  const page = isRecord(ui) && isRecord(ui.page) ? ui.page : null
  const sections = Array.isArray(page?.sections) ? page.sections.filter((section): section is DomainSection => ['workflows', 'saved', 'files'].includes(section)) : []
  return { sections: [...new Set(sections)], description: typeof page?.description === 'string' ? page.description : plugin?.description ?? '' }
}
