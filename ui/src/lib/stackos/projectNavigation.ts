import type { LocationQuery, RouteLocationRaw } from 'vue-router'

const PROJECT_PATH = /^\/projects\/[^/]+(?:\/(.*))?$/

// Query portability is fail-closed. Each project surface declares only the
// controls that still mean the same thing after the project scope changes.
const PORTABLE_QUERY_KEYS_BY_SURFACE: Readonly<Record<string, readonly string[]>> = {
  activity: ['view'],
  connections: ['section'],
  operations: ['operation'],
  'action-calls': ['plugin_slug', 'action_ref'],
  'agent-requests': ['attention_status'],
  tasks: ['view', 'status', 'focus'],
  'workflow-templates': ['plugin_slug'],
  data: ['tab'],
  resources: ['plugin_slug'],
  runs: ['status'],
}

interface ProjectRouteIdentity {
  params: {
    id?: unknown
  }
}

interface ProjectSwitchRoute {
  path: string
  query: LocationQuery
  hash: string
}

function portableQuery(query: LocationQuery, suffix: string): LocationQuery {
  const surface = suffix.split('/')[0] ?? ''
  const allowed = new Set(PORTABLE_QUERY_KEYS_BY_SURFACE[surface] ?? [])
  return Object.fromEntries(Object.entries(query).filter(([key]) => allowed.has(key)))
}

function portableSuffix(rawSuffix: string): string {
  const segments = rawSuffix.split('/').filter(Boolean)
  // A run detail belongs to the previous project. Keep the Runs surface, then
  // let it reload that project's own list rather than opening a stale id.
  if (segments[0] === 'runs' && segments.length > 1) return 'runs'
  return segments.join('/')
}

export function projectIdFromRoute(route: ProjectRouteIdentity): number | null {
  const raw = route.params.id
  const value = Array.isArray(raw) ? raw[0] : raw
  const normalized = String(value ?? '')
  if (!/^[1-9]\d*$/.test(normalized)) return null
  const parsed = Number(normalized)
  return Number.isSafeInteger(parsed) ? parsed : null
}

export function projectSwitchDestination(
  route: ProjectSwitchRoute,
  projectId: number,
): RouteLocationRaw | null {
  const match = route.path.match(PROJECT_PATH)

  // Portfolio selection opens that project's home. Other global surfaces stay
  // put while App.vue updates the local navigation project.
  if (!match) {
    return route.path === '/' ? { path: `/projects/${projectId}` } : null
  }

  const suffix = match[1] ? portableSuffix(match[1]) : ''
  const path = suffix ? `/projects/${projectId}/${suffix}` : `/projects/${projectId}`

  return {
    path,
    query: portableQuery(route.query, suffix),
    hash: route.hash,
  }
}
