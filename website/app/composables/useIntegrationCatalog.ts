import catalog from '~/data/integration-catalog.generated.json'

export interface IntegrationLink {
  key: string
  label: string
  url: string
  confidence: string | null
}

export interface IntegrationAction {
  key: string
  name: string
  description: string
  keywords: string
  capability: string
  capabilityName: string
  risk: string
}

export interface IntegrationLogo {
  src: string
  kind: 'icon' | 'wordmark' | 'wordmark-dark'
}

export interface IntegrationProvider {
  key: string
  slug: string
  providerKey: string
  name: string
  description: string
  authType: string
  pluginSlug: string
  pluginName: string
  pluginDescription: string
  color: string
  logo: IntegrationLogo | null
  actionCount: number
  capabilities: string[]
  actions: IntegrationAction[]
  links: IntegrationLink[]
  primaryUrl: string | null
  docsUrl: string | null
  setupNote: string
}

export interface IntegrationPlugin {
  slug: string
  name: string
  description: string
  color: string
  providerCount: number
  actionCount: number
  capabilityCount: number
  providerSlugs: string[]
  providerNames: string[]
}

export interface IntegrationCounts {
  providers: number
  plugins: number
  actions: number
  trackboothActions: number
}

const providers = catalog.providers as IntegrationProvider[]
const plugins = catalog.plugins as IntegrationPlugin[]
const counts = catalog.counts as IntegrationCounts

export function useIntegrationCatalog() {
  return {
    providers,
    plugins,
    counts,
    generatedAt: catalog.generatedAt,
    providerBySlug: (slug: string) => providers.find((provider) => provider.slug === slug),
    pluginBySlug: (slug: string) => plugins.find((plugin) => plugin.slug === slug),
    providersForPlugin: (slug: string) => providers.filter((provider) => provider.pluginSlug === slug),
  }
}
