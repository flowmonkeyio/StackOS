import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import type {
  SchemaActionOut,
  SchemaAccountOut,
  SchemaAuthCredentialEditOut,
  SchemaAuthCredentialSetRequest,
  SchemaAuthCredentialUpdateRequest,
  SchemaAuthProviderOut,
  SchemaAuthStartRequest,
  SchemaAuthStatusOut,
  SchemaCapabilityOut,
  SchemaCatalogOut,
  SchemaPluginCatalogOut,
  SchemaPluginOut,
  SchemaProviderOut,
  SchemaResourceOut,
  SchemaWriteResponseAuthCredentialSetOut,
  SchemaWriteResponseAuthRevokeOut,
  SchemaWriteResponseAuthStartOut,
  SchemaWriteResponseAuthTestOut,
} from '@/api'
import { apiFetch, formatApiError } from '@/lib/client'

export const useStackOsCatalogStore = defineStore('stackosCatalog', () => {
  const plugins = ref<SchemaPluginOut[]>([])
  const catalog = ref<SchemaCatalogOut | null>(null)
  const capabilities = ref<SchemaCapabilityOut[]>([])
  const providers = ref<SchemaProviderOut[]>([])
  const authProviders = ref<SchemaAuthProviderOut[]>([])
  const globalAccountsStatus = ref<SchemaAuthStatusOut | null>(null)
  const authStatus = ref<SchemaAuthStatusOut | null>(null)
  const actions = ref<SchemaActionOut[]>([])
  const resources = ref<SchemaResourceOut[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  let pluginRefreshSequence = 0
  let connectionRefreshSequence = 0
  let accountRefreshSequence = 0
  let catalogRefreshSequence = 0

  const enabledPlugins = computed(() =>
    plugins.value.filter((plugin) => plugin.enabled_for_project !== false),
  )

  async function refreshPlugins(
    projectId?: number,
    options: { silent?: boolean } = {},
  ): Promise<void> {
    const requestSequence = ++pluginRefreshSequence
    if (!options.silent) loading.value = true
    error.value = null
    if (!options.silent) {
      plugins.value = []
      catalog.value = composeCatalog(
        [],
        capabilities.value,
        providers.value,
        actions.value,
        resources.value,
      )
    }
    try {
      const pluginQuery = projectId ? `?project_id=${projectId}` : ''
      const compactQuery = projectId ? `${pluginQuery}&compact=true` : '?compact=true'
      const pluginRows = await apiFetch<SchemaPluginOut[]>(`/api/v1/plugins${compactQuery}`)
      if (requestSequence !== pluginRefreshSequence) return
      plugins.value = pluginRows
      catalog.value = composeCatalog(
        pluginRows,
        capabilities.value,
        providers.value,
        actions.value,
        resources.value,
      )
    } catch (err) {
      if (requestSequence === pluginRefreshSequence) {
        error.value = formatApiError(err, 'failed to load StackOS plugins')
      }
    } finally {
      if (!options.silent && requestSequence === pluginRefreshSequence) loading.value = false
    }
  }

  async function refresh(projectId?: number): Promise<void> {
    const requestSequence = ++catalogRefreshSequence
    loading.value = true
    error.value = null
    plugins.value = []
    capabilities.value = []
    providers.value = []
    actions.value = []
    resources.value = []
    catalog.value = null
    try {
      const pluginQuery = projectId ? `?project_id=${projectId}` : ''
      const [pluginRows, capabilityRows, providerRows, actionRows, resourceRows] =
        await Promise.all([
          apiFetch<SchemaPluginOut[]>(`/api/v1/plugins${pluginQuery}`),
          apiFetch<SchemaCapabilityOut[]>(`/api/v1/capabilities${pluginQuery}`),
          apiFetch<SchemaProviderOut[]>(`/api/v1/providers${pluginQuery}`),
          apiFetch<SchemaActionOut[]>(`/api/v1/actions${pluginQuery}`),
          apiFetch<SchemaResourceOut[]>(`/api/v1/resources${pluginQuery}`),
        ])
      if (requestSequence !== catalogRefreshSequence) return
      plugins.value = pluginRows
      capabilities.value = capabilityRows
      providers.value = providerRows
      actions.value = actionRows
      resources.value = resourceRows
      catalog.value = composeCatalog(
        pluginRows,
        capabilityRows,
        providerRows,
        actionRows,
        resourceRows,
      )
    } catch (err) {
      if (requestSequence === catalogRefreshSequence) {
        error.value = formatApiError(err, 'failed to load StackOS catalog')
      }
    } finally {
      if (requestSequence === catalogRefreshSequence) loading.value = false
    }
  }

  async function refreshAuth(projectId: number, options: { silent?: boolean } = {}): Promise<void> {
    const requestSequence = ++connectionRefreshSequence
    if (!options.silent) loading.value = true
    error.value = null
    if (!options.silent) {
      authProviders.value = []
      authStatus.value = null
    }
    try {
      const status = await apiFetch<SchemaAuthStatusOut>(
        `/api/v1/projects/${projectId}/connections/accounts`,
      )
      const nextAuthProviders = status.providers
      if (requestSequence !== connectionRefreshSequence) return
      authProviders.value = nextAuthProviders
      authStatus.value = status
    } catch (err) {
      if (requestSequence === connectionRefreshSequence) {
        error.value = formatApiError(err, 'failed to load connections')
      }
    } finally {
      if (!options.silent && requestSequence === connectionRefreshSequence) loading.value = false
    }
  }

  async function refreshAccounts(options: { silent?: boolean } = {}): Promise<void> {
    const requestSequence = ++accountRefreshSequence
    if (!options.silent) loading.value = true
    error.value = null
    if (!options.silent) globalAccountsStatus.value = null
    try {
      const status = await apiFetch<SchemaAuthStatusOut>('/api/v1/auth/accounts')
      if (requestSequence !== accountRefreshSequence) return
      authProviders.value = status.providers
      globalAccountsStatus.value = status
    } catch (err) {
      if (requestSequence === accountRefreshSequence) {
        error.value = formatApiError(err, 'failed to load accounts')
      }
    } finally {
      if (!options.silent && requestSequence === accountRefreshSequence) loading.value = false
    }
  }

  async function storeCredential(
    providerKey: string,
    body: SchemaAuthCredentialSetRequest,
  ): Promise<SchemaWriteResponseAuthCredentialSetOut> {
    error.value = null
    const response = await apiFetch<SchemaWriteResponseAuthCredentialSetOut>(
      `/api/v1/auth/accounts/${providerKey}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
    )
    await refreshAccounts({ silent: true })
    return response
  }

  async function getCredential(credentialRef: string): Promise<SchemaAuthCredentialEditOut> {
    error.value = null
    return apiFetch<SchemaAuthCredentialEditOut>(
      `/api/v1/auth/accounts/${encodeURIComponent(credentialRef)}`,
    )
  }

  async function updateCredential(
    credentialRef: string,
    body: SchemaAuthCredentialUpdateRequest,
  ): Promise<SchemaWriteResponseAuthCredentialSetOut> {
    error.value = null
    const response = await apiFetch<SchemaWriteResponseAuthCredentialSetOut>(
      `/api/v1/auth/accounts/${encodeURIComponent(credentialRef)}`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
    )
    await refreshAccounts({ silent: true })
    return response
  }

  async function startCredential(
    providerKey: string,
    body: SchemaAuthStartRequest,
  ): Promise<SchemaWriteResponseAuthStartOut> {
    error.value = null
    const response = await apiFetch<SchemaWriteResponseAuthStartOut>(
      `/api/v1/auth/accounts/${providerKey}/start`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
    )
    await refreshAccounts({ silent: true })
    return response
  }

  async function testCredential(
    credentialRef: string,
  ): Promise<SchemaWriteResponseAuthTestOut> {
    error.value = null
    const response = await apiFetch<SchemaWriteResponseAuthTestOut>(
      `/api/v1/auth/accounts/${encodeURIComponent(credentialRef)}/test`,
      {
        method: 'POST',
      },
    )
    await refreshAccounts({ silent: true })
    return response
  }

  async function revokeCredential(
    credentialRef: string,
  ): Promise<SchemaWriteResponseAuthRevokeOut> {
    error.value = null
    const response = await apiFetch<SchemaWriteResponseAuthRevokeOut>(
      `/api/v1/auth/accounts/${encodeURIComponent(credentialRef)}/revoke`,
      {
        method: 'POST',
      },
    )
    await refreshAccounts({ silent: true })
    return response
  }

  async function attachAccount(projectId: number, credentialRef: string): Promise<SchemaAccountOut> {
    error.value = null
    const response = await apiFetch<{ data: SchemaAccountOut }>(
      `/api/v1/projects/${projectId}/connections/accounts/${encodeURIComponent(credentialRef)}`,
      { method: 'POST' },
    )
    await Promise.all([
      refreshAccounts({ silent: true }),
      refreshAuth(projectId, { silent: true }),
    ])
    return response.data
  }

  async function detachAccount(projectId: number, credentialRef: string): Promise<SchemaAccountOut> {
    error.value = null
    const response = await apiFetch<{ data: SchemaAccountOut }>(
      `/api/v1/projects/${projectId}/connections/accounts/${encodeURIComponent(credentialRef)}`,
      { method: 'DELETE' },
    )
    await Promise.all([
      refreshAccounts({ silent: true }),
      refreshAuth(projectId, { silent: true }),
    ])
    return response.data
  }

  function actionsFor(pluginSlug: string): SchemaActionOut[] {
    return actions.value.filter((action) => action.plugin_slug === pluginSlug)
  }

  function capabilitiesFor(pluginSlug: string): SchemaCapabilityOut[] {
    return capabilities.value.filter((capability) => capability.plugin_slug === pluginSlug)
  }

  function providersFor(pluginSlug: string): SchemaProviderOut[] {
    return providers.value.filter((provider) => provider.plugin_slug === pluginSlug)
  }

  function resourcesFor(pluginSlug: string): SchemaResourceOut[] {
    return resources.value.filter((resource) => resource.plugin_slug === pluginSlug)
  }

  return {
    plugins,
    catalog,
    capabilities,
    providers,
    authProviders,
    globalAccountsStatus,
    authStatus,
    actions,
    resources,
    loading,
    error,
    enabledPlugins,
    refreshPlugins,
    refresh,
    refreshAuth,
    refreshAccounts,
    storeCredential,
    getCredential,
    updateCredential,
    startCredential,
    testCredential,
    revokeCredential,
    attachAccount,
    detachAccount,
    actionsFor,
    capabilitiesFor,
    providersFor,
    resourcesFor,
  }
})

function composeCatalog(
  plugins: SchemaPluginOut[],
  capabilities: SchemaCapabilityOut[],
  providers: SchemaProviderOut[],
  actions: SchemaActionOut[],
  resources: SchemaResourceOut[],
): SchemaCatalogOut {
  return {
    plugins: plugins
      .filter((plugin) => plugin.enabled_for_project !== false)
      .map<SchemaPluginCatalogOut>((plugin) => ({
        plugin,
        capabilities: capabilities.filter((capability) => capability.plugin_slug === plugin.slug),
        providers: providers.filter((provider) => provider.plugin_slug === plugin.slug),
        actions: actions.filter((action) => action.plugin_slug === plugin.slug),
        resources: resources.filter((resource) => resource.plugin_slug === plugin.slug),
      })),
  }
}
