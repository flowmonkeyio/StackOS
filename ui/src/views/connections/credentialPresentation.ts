import type { SchemaAuthProviderOut, SchemaCredentialConnectionOut } from '@/api'

import type { ConnectionRow, ServiceGroup } from './types'
import { providerSetupGuidance } from './providerSetup'

export { formatAuthType } from '@/lib/stackos/providerPresentation'

const STATUS_ORDER: Record<string, number> = {
  connected: 0,
  pending: 1,
  expired: 2,
  failed: 3,
  revoked: 4,
}

interface ConnectionStatusLike {
  status?: string | null
  setup_required?: boolean | null
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
  const statusDiff = (STATUS_ORDER[left.status] ?? 99) - (STATUS_ORDER[right.status] ?? 99)
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
  return connectionStatusKey(connection) !== 'connected'
}

export function connectionAttentionTone(connection: ConnectionStatusLike): 'danger' | 'warning' {
  return ['failed', 'expired', 'revoked'].includes(connectionStatusKey(connection))
    ? 'danger'
    : 'warning'
}

export function connectionTitle(connection: SchemaCredentialConnectionOut): string {
  return String(connection.label || connection.account?.display_name || connection.profile_key)
}

export function accountLabel(connection: SchemaCredentialConnectionOut): string {
  return String(
    connection.account?.display_name ??
      connection.account?.provider_account_id ??
      connection.profile_key ??
      '-',
  )
}
