import type { SchemaAuthProviderOut } from '@/api'

export interface ProviderSetupLink {
  key: string
  label: string
  url: string
  directional: boolean
}

export interface ProviderSetupGuidance {
  setupNote: string | null
  localSetupLabel: string | null
  localSetupNote: string | null
  callbackUrl: string | null
  callbackNote: string | null
  repairNote: string | null
  verifiedAt: string | null
  links: ProviderSetupLink[]
}

const SETUP_LINKS = [
  ['console_url', 'Provider console'],
  ['credential_url', 'OAuth and credential guide'],
  ['docs_url', 'API documentation'],
  ['support_url', 'Scopes and setup help'],
  ['fallback_url', 'Alternate setup guide'],
] as const

export function providerSetupGuidance(
  provider: SchemaAuthProviderOut,
): ProviderSetupGuidance | null {
  const config = recordValue(provider.config_json)
  const setup = recordValue(config?.setup)
  if (!setup && !textValue(config?.setup_note)) return null
  const confidence = recordValue(setup?.url_confidence)
  const links = SETUP_LINKS.flatMap(([key, label]) => {
    const url = safeHttpsUrl(setup?.[key])
    if (!url) return []
    return [
      {
        key,
        label,
        url,
        directional: textValue(confidence?.[key]) === 'directional',
      },
    ]
  })

  return {
    setupNote: textValue(setup?.setup_note) ?? textValue(config?.setup_note),
    localSetupLabel: textValue(setup?.local_setup_label),
    localSetupNote: textValue(setup?.local_setup_note),
    callbackUrl: safeHttpsUrl(setup?.callback_url),
    callbackNote: textValue(setup?.callback_note),
    repairNote: textValue(setup?.repair_note),
    verifiedAt: textValue(setup?.verified_at),
    links,
  }
}

export function safeHttpsUrl(value: unknown): string | null {
  const text = textValue(value)
  if (!text) return null
  try {
    const url = new URL(text)
    if (url.protocol !== 'https:' || url.username || url.password) return null
    return url.toString()
  } catch {
    return null
  }
}

function recordValue(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  return value as Record<string, unknown>
}

function textValue(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}
