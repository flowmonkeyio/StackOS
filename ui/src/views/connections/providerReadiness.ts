import type { SchemaAuthProviderOut, SchemaCredentialConnectionOut } from '@/api'

export type CapabilityReadinessState =
  | 'ready'
  | 'not-enabled'
  | 'missing-scopes'
  | 'operator-checklist'
  | 'connection-repair'
  | 'pending'

export interface CapabilityReadiness {
  key: string
  label: string
  state: CapabilityReadinessState
  summary: string
  requiredScopes: string[]
  grantedScopes: string[]
  missingScopes: string[]
  prerequisites: string[]
}

const IDENTIFIER_LABELS: Record<string, string> = {
  api: 'API',
  crm: 'CRM',
  id: 'ID',
  oauth: 'OAuth',
  url: 'URL',
}

export function providerCapabilityReadiness(
  provider: SchemaAuthProviderOut,
  connection: SchemaCredentialConnectionOut,
): CapabilityReadiness[] {
  const config = recordValue(provider.config_json)
  const groups = recordValue(config?.readiness_groups)
  const bundles = recordValue(config?.scope_bundles)
  if (!groups) return []

  const granted = new Set(connection.scopes ?? [])
  return Object.entries(groups).flatMap(([key, rawGroup]) => {
    const group = recordValue(rawGroup)
    if (!group) return []

    const sourceBundle = stringValue(group.source_bundle)
    const bundle = sourceBundle ? recordValue(bundles?.[sourceBundle]) : null
    const requiredScopes = uniqueStrings([
      ...stringList(group.required_scopes),
      ...stringList(bundle?.optional_scopes),
    ])
    const grantedScopes = requiredScopes.filter((scope) => granted.has(scope))
    const missingScopes = requiredScopes.filter((scope) => !granted.has(scope))
    const prerequisites = uniqueStrings(stringList(group.prerequisites))
    const state = readinessState({
      connection,
      optionalBundle: Boolean(sourceBundle),
      requiredScopes,
      missingScopes,
      prerequisites,
    })

    return [
      {
        key,
        label: stringValue(group.label) ?? humanizeIdentifier(key),
        state,
        summary: readinessSummary(
          state,
          requiredScopes.length,
          missingScopes.length,
          prerequisites.length,
        ),
        requiredScopes,
        grantedScopes,
        missingScopes,
        prerequisites,
      },
    ]
  })
}

export function humanizeIdentifier(value: string): string {
  return value
    .replace(/[_-]+/g, ' ')
    .split(' ')
    .filter(Boolean)
    .map(
      (part) =>
        IDENTIFIER_LABELS[part.toLowerCase()] ?? part.charAt(0).toUpperCase() + part.slice(1),
    )
    .join(' ')
}

function readinessState({
  connection,
  optionalBundle,
  requiredScopes,
  missingScopes,
  prerequisites,
}: {
  connection: SchemaCredentialConnectionOut
  optionalBundle: boolean
  requiredScopes: string[]
  missingScopes: string[]
  prerequisites: string[]
}): CapabilityReadinessState {
  if (connection.status === 'pending') return 'pending'
  if (
    connection.setup_required ||
    !['connected', 'used'].includes(connection.status) ||
    connection.revoked_at !== null
  ) {
    return 'connection-repair'
  }
  if (missingScopes.length > 0) {
    if (optionalBundle && requiredScopes.length === missingScopes.length) return 'not-enabled'
    return 'missing-scopes'
  }
  if (prerequisites.length > 0) return 'operator-checklist'
  return 'ready'
}

function readinessSummary(
  state: CapabilityReadinessState,
  scopeCount: number,
  missingCount: number,
  prerequisiteCount: number,
): string {
  if (state === 'pending') return 'Authorization is still pending.'
  if (state === 'connection-repair') return 'Repair or reconnect this account first.'
  if (state === 'not-enabled') return 'Reconnect and grant this optional capability bundle.'
  if (state === 'missing-scopes') {
    return `${missingCount} of ${scopeCount} required ${scopeCount === 1 ? 'scope is' : 'scopes are'} missing.`
  }
  if (state === 'operator-checklist') {
    return `OAuth scopes are satisfied; StackOS does not automatically verify these ${prerequisiteCount} operator ${prerequisiteCount === 1 ? 'check' : 'checks'}.`
  }
  return scopeCount > 0
    ? `All ${scopeCount} required ${scopeCount === 1 ? 'scope is' : 'scopes are'} granted.`
    : 'No additional scopes are required.'
}

function recordValue(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  return value as Record<string, unknown>
}

function stringValue(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((item) => {
    const text = stringValue(item)
    return text ? [text] : []
  })
}

function uniqueStrings(values: string[]): string[] {
  return [...new Set(values)]
}
