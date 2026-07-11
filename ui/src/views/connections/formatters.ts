import type { SchemaCredentialConnectionOut } from '@/api'

import type {
  CommunicationProfile,
  CommunicationRoute,
  CommunicationSurface,
  CommunicationTarget,
  ConnectionRow,
  IngressEndpointRoute,
  TelegramCommandDraft,
  TelegramCommandSpec,
} from './types'

export * from './credentialPresentation'

export function communicationProfileTitle(profile: CommunicationProfile): string {
  return profile.identity.display_name || profile.key
}

export function profileProviderKeys(profile: CommunicationProfile): string[] {
  return Object.keys(profile.provider_facets ?? {}).sort()
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

export function routePurpose(route: CommunicationRoute): string {
  const purpose = route.metadata_json?.purpose
  return typeof purpose === 'string' && purpose.trim() ? purpose.trim() : ''
}

/** Plain-language summary of what a handoff route is allowed to carry. */
export function routeFieldSummary(route: CommunicationRoute): string {
  const policy = route.field_policy ?? {}
  const parts: string[] = []
  if (policy.forward_text === true) parts.push('Text')
  if (policy.forward_media === true) parts.push('Media')
  const allowed = Array.isArray(policy.allowed_fields) ? policy.allowed_fields.length : 0
  const redact = Array.isArray(policy.redact_fields) ? policy.redact_fields.length : 0
  if (allowed) parts.push(`${allowed} field${allowed === 1 ? '' : 's'}`)
  if (redact) parts.push(`${redact} redacted`)
  return parts.length > 0 ? parts.join(' · ') : 'Default fields'
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

const SURFACE_AUDIENCE_LABELS: Record<string, string> = {
  internal: 'Internal',
  customer: 'Customer',
  public: 'Public',
  mixed: 'Mixed',
}

/** Humanized audience label (no status.ts domain; surface audience is a descriptor, not a state). */
export function surfaceAudienceLabel(surface: CommunicationSurface): string {
  const audience = surface.audience?.trim()
  if (!audience) return 'Unknown audience'
  return SURFACE_AUDIENCE_LABELS[audience] ?? titleCase(audience)
}

const ROUTE_STATUS_LABELS: Record<string, string> = {
  remote_webhook_updated: 'Webhook ready',
  ready: 'Ready',
  manual_provider_update_required: 'Manual update needed',
}

/** Humanized ingress route status label (route status is not a status.ts domain). */
export function routeStatusLabel(route: IngressEndpointRoute): string {
  const status = route.remote_status?.trim()
  if (!status) return 'Local'
  return ROUTE_STATUS_LABELS[status] ?? titleCase(status)
}

function titleCase(value: string): string {
  return value
    .replace(/[_-]+/g, ' ')
    .split(' ')
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
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

export function profileAudienceMeta(profile: CommunicationProfile): {
  label: 'Chats' | 'Channels'
  count: number
} {
  if (profile.provider_facets?.['slack-bot']) {
    return {
      label: 'Channels',
      count: profile.access_policy.allowed_surface_refs?.length ?? 0,
    }
  }
  return {
    label: 'Chats',
    count: profile.access_policy.allowed_chat_refs?.length ?? 0,
  }
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

export function slackFacet(profile: CommunicationProfile): Record<string, unknown> {
  return profile.provider_facets?.['slack-bot'] ?? {}
}

export function slackProfileAuthKey(profile: CommunicationProfile): string {
  const value = slackFacet(profile)['auth_profile_key']
  return typeof value === 'string' && value ? value : 'default'
}

/** True once a Slack connection has been tested and its workspace identity resolved. */
export function slackIdentified(connection: ConnectionRow | null): boolean {
  const meta = connection?.account?.metadata_json
  return Boolean(meta && typeof meta === 'object' && 'team_id' in meta)
}

/** Build the safe slack-bot facet identity from a tested connection's account. */
export function slackFacetFromConnection(connection: ConnectionRow | null): Record<string, unknown> {
  const meta = (connection?.account?.metadata_json ?? {}) as Record<string, unknown>
  const facet: Record<string, unknown> = {}
  if (typeof meta.team_id === 'string') facet.team_id = meta.team_id
  if (typeof meta.team === 'string') facet.team_name = meta.team
  if (typeof meta.user_id === 'string') facet.bot_user_id = meta.user_id
  if (typeof meta.bot_id === 'string') facet.bot_id = meta.bot_id
  return facet
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

// --- Plain-language helpers for the Messaging sections ---

const PROVIDER_LABELS: Record<string, string> = {
  'telegram-bot': 'Telegram',
  'slack-bot': 'Slack',
  smtp: 'Email (SMTP)',
  imap: 'Email (IMAP)',
  'local-agent-chat': 'Local chat',
}

/** Friendly provider name (e.g. `telegram-bot` -> "Telegram"). */
export function providerLabel(providerKey: string | null | undefined): string {
  if (!providerKey) return 'Unknown'
  return PROVIDER_LABELS[providerKey] ?? titleCase(providerKey.replace(/-bot$/, ''))
}

const CHANNEL_KIND_LABELS: Record<string, string> = {
  'slack-channel': 'Slack channel',
  'slack-private-channel': 'Slack private channel',
  'slack-dm': 'Slack DM',
  'slack-mpim': 'Slack group DM',
  'telegram-private': 'Telegram DM',
  'telegram-group': 'Telegram group',
  'telegram-supergroup': 'Telegram group',
  'telegram-channel': 'Telegram channel',
  'smtp-identity': 'Email identity',
  'imap-mailbox': 'Mailbox',
  'local-agent-chat': 'Local chat',
}

/** Friendly channel kind (e.g. `telegram-supergroup` -> "Telegram group"). */
export function channelKindLabel(surface: CommunicationSurface): string {
  return CHANNEL_KIND_LABELS[surface.kind] ?? titleCase(surface.kind)
}

/** First (primary) provider a bot profile is bound to, e.g. `telegram-bot`. */
export function profilePrimaryProvider(profile: CommunicationProfile): string {
  return profileProviderKeys(profile)[0] ?? ''
}

export interface SensitivityMeta {
  label: string
  tone: 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'accent'
  hint: string
}

const SENSITIVITY_META: Record<string, SensitivityMeta> = {
  'customer-confidential': {
    label: 'Customer-confidential',
    tone: 'warning',
    hint: 'Don’t share outside this channel without approval.',
  },
  internal: {
    label: 'Internal',
    tone: 'info',
    hint: 'Internal coordination only — not customer-visible.',
  },
  public: {
    label: 'Public',
    tone: 'success',
    hint: 'Safe to share publicly.',
  },
}

/**
 * Plain-language reading of a channel's `data_scope.classification` (the
 * "sensitivity" the operator controls): a label, a badge tone, and a one-line
 * sharing hint.
 */
export function sensitivityMeta(surface: CommunicationSurface): SensitivityMeta {
  const classification = String(surface.data_scope?.classification ?? '').trim()
  return (
    SENSITIVITY_META[classification] ?? {
      label: classification ? titleCase(classification) : 'Not set',
      tone: 'neutral',
      hint: 'No data-sharing guidance configured for this channel yet.',
    }
  )
}
