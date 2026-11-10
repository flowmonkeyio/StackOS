import type { SchemaArtifactOut, SchemaDecisionOut, SchemaExperimentOut, SchemaLearningOut, SchemaResourceOut, SchemaResourceRecordOut } from '@/api'
import type { UiFactGroup } from '@/components/ui/UiFactGroups.vue'
import { sanitizeForDisplay, shortValue } from '@/lib/stackos/json'

export interface LibraryPayloads {
  resource_record: SchemaResourceRecordOut
  decision: SchemaDecisionOut
  learning: SchemaLearningOut
  experiment: SchemaExperimentOut
  artifact: SchemaArtifactOut
}
export type LibrarySourceKind = keyof LibraryPayloads
export type LibrarySource = { [K in LibrarySourceKind]: {
  source_kind: K
  source_id: number
  project_id: number | null
  plugin_slug: string | null
  resource_key: string | null
  identity: string
  read_ref: string
  title: string
  payload: LibraryPayloads[K]
} }[LibrarySourceKind]

/** A view of the canonical row, never a persisted copy or a title-based join. */
export function toLibrarySource<K extends LibrarySourceKind>(kind: K, payload: LibraryPayloads[K]): Extract<LibrarySource, { source_kind: K }> {
  const paths = { resource_record: 'resource-records', artifact: 'artifacts', decision: 'decisions', learning: 'learnings', experiment: 'experiments' }
  const row = payload as LibraryPayloads[LibrarySourceKind]
  const title = 'title' in row ? row.title : 'name' in row ? row.name : 'statement' in row ? row.statement : null
  return {
    source_kind: kind, source_id: row.id, project_id: row.project_id,
    plugin_slug: 'plugin_slug' in row ? row.plugin_slug : null,
    resource_key: 'resource_key' in row ? row.resource_key : null,
    identity: `${kind}:${row.id}`,
    read_ref: kind === 'resource_record' || kind === 'artifact'
      ? `/api/v1/${paths[kind]}/${row.id}`
      : `/api/v1/projects/${row.project_id}/${paths[kind]}`,
    title: String(sanitizeForDisplay(title || `${kind} #${row.id}`)),
    payload,
  } as Extract<LibrarySource, { source_kind: K }>
}

function object(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}
function fields(value: unknown): string[] {
  return Array.isArray(value) ? [...new Set(value.filter((item): item is string => typeof item === 'string' && !!item))] : []
}
function label(key: string) { return key.replaceAll('_', ' ').replace(/^./, (c) => c.toUpperCase()) }
function factGroup(title: string, data: Record<string, unknown>, keys: string[], wide = false): UiFactGroup {
  return { title, items: keys.map((key) => {
    const field = Object.prototype.hasOwnProperty.call(data, key) ? data[key] : undefined
    const formatted = shortValue(field)
    const value = typeof formatted === 'string' ? formatted : '-'
    return { label: label(key), value, wide: wide || ['decision', 'rationale', 'statement', 'hypothesis'].includes(key) || value.length > 80 || typeof field === 'object' && field !== null }
  }) }
}

export function sourceFactGroups(source: LibrarySource, resource?: SchemaResourceOut | null): UiFactGroup[] {
  const data = object(sanitizeForDisplay(source.source_kind === 'resource_record' ? source.payload.data_json : source.payload))
  if (source.source_kind === 'resource_record') {
    // A schema from another record must never provide its labels or field hints.
    const schema = resource?.id === source.payload.resource_id && resource.plugin_slug === source.plugin_slug && resource.key === source.resource_key
      ? object(resource.ui_schema_json) : {}
    const groups: UiFactGroup[] = []
    const shown = new Set<string>()
    // First placement wins: summary, facts, then additional detail fields.
    function addGroup(title: string, hints: unknown, wide = false) {
      const keys = fields(hints).filter(key => !shown.has(key))
      if (!keys.length) return
      keys.forEach(key => shown.add(key))
      groups.push(factGroup(title, data, keys, wide))
    }
    addGroup('Summary', schema.summary_fields, true)
    addGroup('Facts', schema.table_columns)
    if (Array.isArray(schema.detail_sections)) {
      for (const section of schema.detail_sections) {
        const hint = object(section)
        if (typeof hint.title === 'string') addGroup(String(sanitizeForDisplay(hint.title)), hint.fields)
      }
    }
    return groups
  }
  const keys: Record<Exclude<LibrarySourceKind, 'resource_record'>, string[]> = {
    decision: ['decision', 'rationale', 'status', 'decided_by', 'run_id', 'experiment_id'],
    learning: ['statement', 'status', 'review_state', 'confidence', 'domain', 'supersedes_learning_id', 'source_snapshot_id'],
    experiment: ['hypothesis', 'status', 'domain', 'variants', 'metric_targets_json', 'linked_run_ids_json'],
    artifact: ['kind', 'mime_type', 'size_bytes', 'status', 'superseded_by_artifact_id', 'resource_record_id'],
  }
  return [factGroup('Saved content', data, keys[source.source_kind])]
}

export function artifactReference(artifact: SchemaArtifactOut): { href: string | null; preview: boolean; reason: string; display_uri: string } {
  if (artifact.plugin_slug === 'finance') return { href: null, preview: false, reason: 'Finance files remain available through Finance.', display_uri: '[private reference]' }
  const uri = artifact.uri
  const sanitized = String(sanitizeForDisplay(uri))
  let url: URL | null = null
  try { url = new URL(uri) } catch { /* Unparseable references have no action capability. */ }
  // Classify display safety for every scheme, including malformed authorities.
  const queryStart = uri.indexOf('?')
  const query = new URLSearchParams(queryStart < 0 ? '' : uri.slice(queryStart + 1).split('#')[0])
  const hasCredentialQuery = [...query.keys()].some((key) =>
    /^sig$/i.test(key) || /token|secret|password|signature|credential|api.?key|access.?key|authorization/i.test(key))
  const authority = uri.trim().replace(/[\t\r\n]/g, '')
  const hasUserinfo = /^(?:[a-z][a-z\d+.-]*:)?[/\\]{2,}[^/\\?#]*@/i.test(authority) || Boolean(url?.username || url?.password)
  const sensitive = sanitized !== uri || hasUserinfo || hasCredentialQuery
  const unavailable = {
    href: null, preview: false, reason: 'Reference unavailable for opening or preview.',
    display_uri: sensitive ? '[redacted reference]' : sanitized,
  }
  if (sensitive) return unavailable
  if (!url || !uri || [...uri].some((char) => char.charCodeAt(0) < 33) || /\s/.test(uri)) return unavailable
  if (!['https:', 'http:'].includes(url.protocol)) return unavailable
  return { href: url.href, preview: ['image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/avif'].includes(artifact.mime_type ?? ''), reason: 'External reference; availability has not been checked.', display_uri: sanitized }
}
