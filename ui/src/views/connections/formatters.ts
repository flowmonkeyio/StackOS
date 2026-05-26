import type { SchemaAuthProviderOut, SchemaCredentialConnectionOut } from '@/api'

import type {
  BadgeTone,
  CommunicationProfile,
  CommunicationSurface,
  CommunicationTarget,
  ConnectionRow,
  IngressEndpointRoute,
  ServiceGroup,
  TelegramCommandDraft,
  TelegramCommandSpec,
} from './types'

const AUTH_TYPE_LABELS: Record<string, string> = {
  'api-key': 'API key',
  'application-password': 'Application password',
  basic: 'Username and password',
  local: 'Local',
  none: 'No auth',
  oauth: 'OAuth2',
  'oauth-client-credentials': 'OAuth2 client credentials',
}

const STATUS_ORDER: Record<string, number> = {
  connected: 0,
  pending: 1,
  expired: 2,
  failed: 3,
  revoked: 4,
}

const PLUGIN_LABELS: Record<string, string> = {
  gtm: 'GTM',
  'media-buying': 'Media Buying',
  seo: 'SEO',
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

export function providerSetupNote(provider: SchemaAuthProviderOut): string | null {
  const value = provider.config_json?.setup_note
  return typeof value === 'string' && value.trim() ? value : null
}

export function formatAuthType(authType: string | null | undefined): string {
  if (!authType) return 'Auth'
  return AUTH_TYPE_LABELS[authType] ?? authType
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

export function serviceStatusTone(group: ServiceGroup): BadgeTone {
  if (
    group.connections.some(
      (connection) => connection.status === 'connected' && connection.revoked_at === null,
    )
  ) {
    return 'success'
  }
  if (group.connections.some((connection) => ['failed', 'revoked'].includes(connection.status))) {
    return 'danger'
  }
  return 'warning'
}

export function serviceStatusDotClass(group: ServiceGroup): string {
  const tone = serviceStatusTone(group)
  if (tone === 'success') return 'bg-success'
  if (tone === 'danger') return 'bg-danger'
  if (tone === 'warning') return 'bg-warning'
  return 'bg-neutral'
}

export function serviceStatusLabel(group: ServiceGroup): string {
  const connected = group.connections.filter(
    (connection) => connection.status === 'connected' && connection.revoked_at === null,
  ).length
  if (connected > 0) return `${connected} connected`
  const first = group.connections[0]
  return first ? first.status : 'not connected'
}

export function connectionCountLabel(group: ServiceGroup): string {
  const count = group.connections.length
  return `${count} connection${count === 1 ? '' : 's'}`
}

export function statusTone(connection: SchemaCredentialConnectionOut): BadgeTone {
  if (connection.status === 'connected' && !connection.setup_required) return 'success'
  if (connection.status === 'failed' || connection.status === 'revoked') return 'danger'
  return 'warning'
}

export function statusDotClass(connection: SchemaCredentialConnectionOut): string {
  const tone = statusTone(connection)
  if (tone === 'success') return 'bg-success'
  if (tone === 'danger') return 'bg-danger'
  if (tone === 'warning') return 'bg-warning'
  return 'bg-neutral'
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

export function communicationProfileTitle(profile: CommunicationProfile): string {
  return profile.identity.display_name || profile.key
}

export function profileProviderKeys(profile: CommunicationProfile): string[] {
  return Object.keys(profile.provider_facets ?? {}).sort()
}

export function allowedOperatorRefs(profile: CommunicationProfile): string[] {
  return profile.access_policy.allowed_user_refs ?? []
}

export function targetTitle(target: CommunicationTarget): string {
  return target.display_name || target.key
}

export function targetPolicySummary(target: CommunicationTarget): string {
  const policy = target.send_policy ?? {}
  const parts = [
    ...(policy.allowed_profile_refs ?? []),
    ...(policy.allowed_invoker_refs ?? []),
    ...(policy.allowed_source_surface_refs ?? []),
    ...(policy.allowed_target_refs ?? []),
  ]
  if (policy.mode === 'deny') return 'disabled'
  if (policy.requires_approval) return 'approval required'
  return parts.length > 0 ? `${parts.length} guard${parts.length === 1 ? '' : 's'}` : 'unscoped'
}

export function surfaceTitle(surface: CommunicationSurface): string {
  return surface.display_name || surface.surface_ref
}

export function surfaceIntentSummary(surface: CommunicationSurface): string {
  const summary = surface.intent.summary
  const category = surface.intent.category
  if (typeof summary === 'string' && summary.trim()) return summary
  if (typeof category === 'string' && category.trim()) return category
  return 'No intent configured'
}

export function surfaceDataScope(surface: CommunicationSurface): string {
  const classification = surface.data_scope.classification
  return typeof classification === 'string' && classification.trim()
    ? classification
    : 'scope unset'
}

export function surfaceAudienceTone(surface: CommunicationSurface): BadgeTone {
  if (surface.audience === 'internal') return 'success'
  if (surface.audience === 'customer' || surface.audience === 'public') return 'warning'
  if (surface.audience === 'mixed') return 'danger'
  return 'neutral'
}

export function routeStatusTone(route: IngressEndpointRoute): BadgeTone {
  if (route.remote_status === 'remote_webhook_updated' || route.remote_status === 'ready') {
    return 'success'
  }
  if (route.remote_status === 'manual_provider_update_required') return 'warning'
  if (route.remote_status?.includes('failed')) return 'danger'
  return 'neutral'
}

export function connectionActionKey(credentialRef: string, action: string): string {
  return `${credentialRef}:${action}`
}

export function providerActionKey(providerKey: string, action: string): string {
  return `${providerKey}:${action}`
}

export function metadataText(metadata: Record<string, unknown> | undefined, key: string): string {
  const value = metadata?.[key]
  return typeof value === 'string' ? value.trim() : ''
}

export function credentialDiscoveryLabel(
  providerKey: string,
  metadata: Record<string, unknown> | undefined,
): string {
  if (providerKey === 'telegram-bot') {
    const username = metadataText(metadata, 'username')
    return username ? `@${username}` : metadataText(metadata, 'first_name')
  }
  if (providerKey === 'slack-bot') {
    return (
      metadataText(metadata, 'team') ||
      metadataText(metadata, 'team_id') ||
      metadataText(metadata, 'user') ||
      metadataText(metadata, 'user_id') ||
      metadataText(metadata, 'bot_id')
    )
  }
  return ''
}

export function credentialTestMessage(
  providerKey: string,
  metadata: Record<string, unknown> | undefined,
  fallback: string,
): string {
  const label = credentialDiscoveryLabel(providerKey, metadata)
  if (!label) return fallback
  if (providerKey === 'telegram-bot') return `Telegram bot verified as ${label}.`
  if (providerKey === 'slack-bot') return `Slack bot verified for ${label}.`
  return fallback
}

export function parseCsv(value: string): string[] {
  return value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
}

export function normalizeCommand(value: string): string {
  const command = value.trim()
  if (!command) return ''
  return command.startsWith('/') ? command : `/${command}`
}

export function toCommandDrafts(commands: TelegramCommandSpec[]): TelegramCommandDraft[] {
  const drafts = commands.map((command) => ({
    command: normalizeCommand(command.command),
    description: command.description ?? '',
    guidance: command.guidance ?? '',
    enabled: command.enabled !== false,
  }))
  return drafts.length > 0
    ? drafts
    : [
        {
          command: '',
          description: '',
          guidance: '',
          enabled: true,
        },
      ]
}

export function toCommandSpecs(commands: TelegramCommandDraft[]): TelegramCommandSpec[] {
  return commands
    .map((command) => ({
      command: normalizeCommand(command.command),
      description: command.description.trim(),
      guidance: command.guidance.trim(),
      enabled: command.enabled,
    }))
    .filter((command) => command.command)
}

export function commandSummary(commands: TelegramCommandSpec[] | undefined): string {
  const values = (commands ?? [])
    .filter((command) => command.enabled !== false)
    .map((command) => command.command)
    .filter(Boolean)
  return values.length > 0 ? values.join(', ') : '-'
}

export function telegramFacet(profile: CommunicationProfile): Record<string, unknown> {
  return profile.provider_facets?.['telegram-bot'] ?? {}
}

export function telegramFacetString(profile: CommunicationProfile, key: string): string {
  const value = telegramFacet(profile)[key]
  return typeof value === 'string' ? value : ''
}

export function telegramProfileAuthKey(profile: CommunicationProfile): string {
  return telegramFacetString(profile, 'auth_profile_key') || 'default'
}

export function telegramProfileUsername(profile: CommunicationProfile): string {
  return telegramFacetString(profile, 'bot_username').replace(/^@/, '')
}

export function telegramProfileIngressMode(profile: CommunicationProfile): string {
  return telegramFacetString(profile, 'ingress_mode') || 'not configured'
}

export function telegramCommands(profile: CommunicationProfile): TelegramCommandSpec[] {
  const commands = profile.trigger_policy['commands']
  return Array.isArray(commands) ? (commands as TelegramCommandSpec[]) : []
}

export function telegramConnectionForProfile(
  profileKey: string,
  telegramConnections: ConnectionRow[],
): ConnectionRow | null {
  return telegramConnections.find((connection) => connection.profile_key === profileKey) ?? null
}

export function botUsernameFromConnection(
  connection: SchemaCredentialConnectionOut | null,
): string | null {
  const metadata = connection?.account?.metadata_json
  const username =
    metadata && typeof metadata === 'object' && 'username' in metadata
      ? String((metadata as Record<string, unknown>).username ?? '').trim()
      : ''
  if (username) return username.replace(/^@/, '')
  const displayName = String(connection?.account?.display_name ?? '').trim()
  return displayName.startsWith('@') ? displayName.slice(1) : null
}

export function preferredTelegramConnection(
  identifiedTelegramConnections: ConnectionRow[],
  telegramConnections: ConnectionRow[],
): ConnectionRow | null {
  return identifiedTelegramConnections[0] ?? telegramConnections[0] ?? null
}
