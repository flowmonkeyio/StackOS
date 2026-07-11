import type { LocationQuery, RouteLocationNormalizedLoaded, RouteLocationRaw } from 'vue-router'

const PROJECT_PATH = /^\/projects\/[^/]+(?:\/(.*))?$/

// These values identify an object inside one project. Carrying them to another
// project creates a confusing empty or incorrect detail state. Surface-level
// controls such as section, view, status, and focus are intentionally retained.
const PROJECT_OBJECT_QUERY_KEYS = new Set([
  'item',
  'task',
  'ticket',
  'request',
  'run',
  'run_id',
  'provider_key',
  'credential_id',
  'profile_id',
  'artifact_id',
  'resource_id',
  'action_call_id',
])

function portableQuery(query: LocationQuery): LocationQuery {
  return Object.fromEntries(
    Object.entries(query).filter(([key]) => !PROJECT_OBJECT_QUERY_KEYS.has(key)),
  )
}

function portableSuffix(rawSuffix: string): string {
  const segments = rawSuffix.split('/').filter(Boolean)
  // A run detail belongs to the previous project. Keep the Runs surface, then
  // let it reload that project's own list rather than opening a stale id.
  if (segments[0] === 'runs' && segments.length > 1) return 'runs'
  return segments.join('/')
}

export function projectSwitchDestination(
  route: Pick<RouteLocationNormalizedLoaded, 'path' | 'query' | 'hash'>,
  projectId: number,
): RouteLocationRaw {
  const match = route.path.match(PROJECT_PATH)
  const suffix = match?.[1] ? portableSuffix(match[1]) : ''
  const path = suffix ? `/projects/${projectId}/${suffix}` : `/projects/${projectId}`

  return {
    path,
    query: match ? portableQuery(route.query) : {},
    hash: match ? route.hash : '',
  }
}
