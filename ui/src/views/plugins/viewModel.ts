import type {
  SchemaActionOut,
  SchemaCapabilityOut,
  SchemaPluginOut,
  SchemaProviderOut,
} from '@/api'

export interface PluginDirectoryItem {
  plugin: SchemaPluginOut
  providers: SchemaProviderOut[]
  actions: SchemaActionOut[]
  capabilities: SchemaCapabilityOut[]
  searchText: string
}

export interface ProviderDirectoryItem {
  provider: SchemaProviderOut
  plugin: SchemaPluginOut
  actions: SchemaActionOut[]
  capabilityNames: string[]
  searchText: string
}

export type CatalogSort = 'name' | 'actions'

export function buildPluginDirectory(
  plugins: SchemaPluginOut[],
  providers: SchemaProviderOut[],
  actions: SchemaActionOut[],
  capabilities: SchemaCapabilityOut[],
): PluginDirectoryItem[] {
  return plugins.map((plugin) => {
    const pluginProviders = providers.filter((provider) => provider.plugin_slug === plugin.slug)
    const pluginActions = actions.filter((action) => action.plugin_slug === plugin.slug)
    const pluginCapabilities = capabilities.filter(
      (capability) => capability.plugin_slug === plugin.slug,
    )
    return {
      plugin,
      providers: pluginProviders,
      actions: pluginActions,
      capabilities: pluginCapabilities,
      searchText: searchableText([
        plugin.name,
        plugin.slug,
        plugin.description,
        ...pluginProviders.flatMap((provider) => [
          provider.name,
          provider.key,
          provider.description,
        ]),
        ...pluginActions.flatMap((action) => [
          action.name,
          action.key,
          action.description,
          action.operation,
        ]),
        ...pluginCapabilities.flatMap((capability) => [
          capability.name,
          capability.key,
          capability.description,
        ]),
      ]),
    }
  })
}

export function buildProviderDirectory(
  pluginItems: PluginDirectoryItem[],
): ProviderDirectoryItem[] {
  return pluginItems.flatMap((item) =>
    item.providers.map((provider) => {
      const providerActions = item.actions.filter(
        (action) => action.provider_id === provider.id || action.provider_key === provider.key,
      )
      const capabilityKeys = new Set(
        providerActions.map((action) => action.capability_key).filter(Boolean),
      )
      const capabilityNames = item.capabilities
        .filter((capability) => capabilityKeys.has(capability.key))
        .map((capability) => capability.name)
        .sort((left, right) => left.localeCompare(right))
      return {
        provider,
        plugin: item.plugin,
        actions: providerActions,
        capabilityNames,
        searchText: searchableText([
          provider.name,
          provider.key,
          provider.description,
          provider.auth_type,
          item.plugin.name,
          item.plugin.slug,
          ...providerActions.flatMap((action) => [
            action.name,
            action.key,
            action.description,
            action.operation,
          ]),
          ...capabilityNames,
        ]),
      }
    }),
  )
}

export function filterAndSortPlugins(
  items: PluginDirectoryItem[],
  query: string,
  sort: CatalogSort,
): PluginDirectoryItem[] {
  const normalized = normalizeQuery(query)
  const filtered = items.filter((item) => !normalized || item.searchText.includes(normalized))
  return [...filtered].sort((left, right) =>
    compareCatalogItems(
      left.plugin.name,
      left.actions.length,
      right.plugin.name,
      right.actions.length,
      sort,
    ),
  )
}

export function filterAndSortProviders(
  items: ProviderDirectoryItem[],
  query: string,
  sort: CatalogSort,
): ProviderDirectoryItem[] {
  const normalized = normalizeQuery(query)
  const filtered = items.filter((item) => !normalized || item.searchText.includes(normalized))
  return [...filtered].sort((left, right) =>
    compareCatalogItems(
      left.provider.name,
      left.actions.length,
      right.provider.name,
      right.actions.length,
      sort,
    ),
  )
}

function compareCatalogItems(
  leftName: string,
  leftActionCount: number,
  rightName: string,
  rightActionCount: number,
  sort: CatalogSort,
): number {
  if (sort === 'actions' && leftActionCount !== rightActionCount) {
    return rightActionCount - leftActionCount
  }
  return leftName.localeCompare(rightName)
}

function searchableText(values: Array<string | null | undefined>): string {
  return values.filter(Boolean).join(' ').toLowerCase()
}

function normalizeQuery(value: string): string {
  return value.trim().toLowerCase()
}
