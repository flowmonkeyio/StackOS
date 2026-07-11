import { describe, expect, it } from 'vitest'

import type {
  SchemaActionOut,
  SchemaCapabilityOut,
  SchemaPluginOut,
  SchemaProviderOut,
} from '@/api'

import {
  buildPluginDirectory,
  buildProviderDirectory,
  filterAndSortPlugins,
  filterAndSortProviders,
} from './viewModel'

describe('plugin directory view model', () => {
  const plugins = [plugin(1, 'utils', 'Utilities'), plugin(2, 'seo', 'SEO')]
  const providers = [
    provider(11, 1, 'utils', 'firecrawl', 'Firecrawl'),
    provider(12, 2, 'seo', 'ahrefs', 'Ahrefs'),
  ]
  const capabilities = [
    capability(21, 1, 'utils', 'crawl', 'Web crawling'),
    capability(22, 2, 'seo', 'keywords', 'Keyword research'),
  ]
  const actions = [
    action(31, 1, 'utils', 11, 'firecrawl', 'crawl', 'Crawl website'),
    action(32, 1, 'utils', 11, 'firecrawl', 'crawl', 'Map website'),
    action(33, 2, 'seo', 12, 'ahrefs', 'keywords', 'Find keywords'),
  ]

  it('searches plugins through provider and action content', () => {
    const directory = buildPluginDirectory(plugins, providers, actions, capabilities)

    expect(
      filterAndSortPlugins(directory, 'Firecrawl', 'name').map((item) => item.plugin.slug),
    ).toEqual(['utils'])
    expect(
      filterAndSortPlugins(directory, 'find keywords', 'name').map((item) => item.plugin.slug),
    ).toEqual(['seo'])
  })

  it('builds provider action counts and sorts richest entries first', () => {
    const providerDirectory = buildProviderDirectory(
      buildPluginDirectory(plugins, providers, actions, capabilities),
    )

    expect(
      filterAndSortProviders(providerDirectory, '', 'actions').map((item) => item.provider.key),
    ).toEqual(['firecrawl', 'ahrefs'])
    expect(
      providerDirectory.find((item) => item.provider.key === 'firecrawl')?.capabilityNames,
    ).toEqual(['Web crawling'])
  })
})

function plugin(id: number, slug: string, name: string): SchemaPluginOut {
  return {
    id,
    slug,
    name,
    version: '1.0.0',
    description: `${name} tools`,
    source: 'builtin' as SchemaPluginOut['source'],
    manifest_json: {},
    enabled_for_project: true,
    created_at: '2026-07-11T00:00:00Z',
    updated_at: '2026-07-11T00:00:00Z',
  }
}

function provider(
  id: number,
  pluginId: number,
  pluginSlug: string,
  key: string,
  name: string,
): SchemaProviderOut {
  return {
    id,
    plugin_id: pluginId,
    plugin_slug: pluginSlug,
    key,
    name,
    description: `${name} provider`,
    auth_type: 'api-key',
    config_json: null,
  }
}

function capability(
  id: number,
  pluginId: number,
  pluginSlug: string,
  key: string,
  name: string,
): SchemaCapabilityOut {
  return {
    id,
    plugin_id: pluginId,
    plugin_slug: pluginSlug,
    key,
    name,
    description: `${name} capability`,
    kind: 'tool',
    config_json: null,
  }
}

function action(
  id: number,
  pluginId: number,
  pluginSlug: string,
  providerId: number,
  providerKey: string,
  capabilityKey: string,
  name: string,
): SchemaActionOut {
  return {
    id,
    plugin_id: pluginId,
    plugin_slug: pluginSlug,
    provider_id: providerId,
    provider_key: providerKey,
    key: name.toLowerCase().replaceAll(' ', '-'),
    name,
    description: `${name} action`,
    action_ref: `${pluginSlug}.${name.toLowerCase().replaceAll(' ', '-')}`,
    capability_key: capabilityKey,
    risk_level: 'low',
    operation: 'read',
    connector_key: providerKey,
    input_schema_json: {},
    output_schema_json: {},
    config_json: null,
    requires_credential: true,
    allows_credential: true,
    enforce_budget: false,
    availability: {} as SchemaActionOut['availability'],
    exposure: {} as SchemaActionOut['exposure'],
  }
}
