import type { SchemaAccountOut, SchemaAuthProviderOut, SchemaAuthTestOut } from '@/api'

import type { ConnectionRow, ServiceGroup } from './types'
import { providerSetupGuidance } from './providerSetup'

export { formatAuthType } from '@/lib/stackos/providerPresentation'

const STATUS_ORDER: Record<string, number> = {
  failed: 0,
  expired: 1,
  revoked: 2,
  'setup-required': 3,
  pending: 4,
  connected: 5,
}

interface ConnectionStatusLike {
  status?: string | null
  setup_required?: boolean | null
  last_test?: Pick<SchemaAuthTestOut, 'ok'> | null
}

const PLUGIN_LABELS: Record<string, string> = {
  gtm: 'GTM',
  'media-buying': 'Media Buying',
  seo: 'SEO',
}

const PROVIDER_CATEGORY_LABELS: Record<string, string> = {
  trackbooth: 'Affiliation',
}

export function pluginLabel(slug: string | null | undefined): string {
  if (!slug) return 'StackOS'
  if (PLUGIN_LABELS[slug]) return PLUGIN_LABELS[slug]
  return slug
    .split('-')
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

export function providerGroupLabel(provider: SchemaAuthProviderOut): string {
  const configuredCategory = provider.config_json?.connection_category
  if (typeof configuredCategory === 'string' && configuredCategory.trim()) {
    return configuredCategory.trim()
  }
  return PROVIDER_CATEGORY_LABELS[provider.key] ?? pluginLabel(provider.plugin_slug)
}

export function providerSetupNote(provider: SchemaAuthProviderOut): string | null {
  return providerSetupGuidance(provider)?.setupNote ?? null
}

export function methodLabel(provider: SchemaAuthProviderOut, methodKey: string): string {
  return provider.auth_methods?.find((method) => method.key === methodKey)?.label ?? methodKey
}

export function compareConnections(left: ConnectionRow, right: ConnectionRow): number {
  const attentionDiff =
    Number(!connectionNeedsAttention(left)) - Number(!connectionNeedsAttention(right))
  if (attentionDiff !== 0) return attentionDiff
  const statusDiff =
    (STATUS_ORDER[connectionStatusKey(left)] ?? 99) -
    (STATUS_ORDER[connectionStatusKey(right)] ?? 99)
  if (statusDiff !== 0) return statusDiff
  return connectionTitle(left).localeCompare(connectionTitle(right))
}

export function serviceName(group: ServiceGroup): string {
  return group.provider?.name ?? group.providerKey
}

export function connectionStatusKey(connection: ConnectionStatusLike): string {
  if (connection.status === 'connected' && connection.setup_required) return 'setup-required'
  return connection.status ?? 'pending'
}

export function connectionNeedsAttention(connection: ConnectionStatusLike): boolean {
  return connectionStatusKey(connection) !== 'connected' || connection.last_test?.ok === false
}

export function connectionAttentionTone(connection: ConnectionStatusLike): 'danger' | 'warning' {
  return connection.last_test?.ok === false ||
    ['failed', 'expired', 'revoked'].includes(connectionStatusKey(connection))
    ? 'danger'
    : 'warning'
}

export function credentialVerificationMessage(
  result: Pick<SchemaAuthTestOut, 'summary' | 'next_action'>,
): string {
  return [result.summary, result.next_action].filter(Boolean).join(' ')
}

export function connectionTitle(connection: SchemaAccountOut): string {
  return connection.display_name
}

export function accountLabel(connection: SchemaAccountOut): string {
  return String(
    connection.account?.display_name ??
      connection.account?.provider_account_id ??
      connection.display_name ??
      '-',
  )
}
