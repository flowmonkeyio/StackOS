import { canonicalPath } from './siteSeo'

export const integrationConsolidations = {
  core: 'local-daemon',
  linear: 'linear',
  shopify: 'shopify',
  trackbooth: 'trackbooth',
} as const

export type ConsolidatedIntegrationPlugin = keyof typeof integrationConsolidations

export function consolidatedProviderSlug(pluginSlug: string) {
  return integrationConsolidations[pluginSlug as ConsolidatedIntegrationPlugin] || null
}

export function providerIntegrationPath(providerSlug: string) {
  return canonicalPath(`/library/integrations/${providerSlug}`)
}

export function pluginIntegrationPath(pluginSlug: string) {
  const providerSlug = consolidatedProviderSlug(pluginSlug)
  return providerSlug
    ? providerIntegrationPath(providerSlug)
    : canonicalPath(`/library/integrations/plugins/${pluginSlug}`)
}

export function isConsolidatedPlugin(pluginSlug: string) {
  return Boolean(consolidatedProviderSlug(pluginSlug))
}
