import providerPresentation from '../../../../provider-presentation.json'

export interface ProviderLogo {
  src: string
  kind: 'icon' | 'wordmark' | 'wordmark-dark'
}

const PROVIDER_LOGOS = providerPresentation as Record<string, ProviderLogo>
const AUTH_TYPE_LABELS: Record<string, string> = {
  'api-key': 'API key',
  'application-password': 'Application password',
  basic: 'Username and password',
  local: 'Local',
  mixed: 'Mixed',
  none: 'No auth',
  oauth: 'OAuth 2.0',
  'oauth-client-credentials': 'OAuth 2.0 client credentials',
}

export function providerLogo(providerKey: string, pluginSlug?: string | null): ProviderLogo | null {
  const exact = pluginSlug ? PROVIDER_LOGOS[`${pluginSlug}.${providerKey}`] : undefined
  if (exact) return exact
  return (
    Object.entries(PROVIDER_LOGOS).find(([key]) => key.endsWith(`.${providerKey}`))?.[1] ?? null
  )
}

export function formatAuthType(authType: string | null | undefined): string {
  if (!authType) return 'Auth'
  return AUTH_TYPE_LABELS[authType] ?? authType
}
