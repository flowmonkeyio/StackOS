import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import type {
  SchemaActionOut,
  SchemaAuthCredentialSetRequest,
  SchemaAuthProviderOut,
  SchemaAuthRevokeRequest,
  SchemaAuthStartRequest,
  SchemaAuthStatusOut,
  SchemaAuthTestRequest,
  SchemaCapabilityOut,
  SchemaCatalogOut,
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
  const authStatus = ref<SchemaAuthStatusOut | null>(null)
  const actions = ref<SchemaActionOut[]>([])
  const resources = ref<SchemaResourceOut[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  const enabledPlugins = computed(() =>
    plugins.value.filter((plugin) => plugin.enabled_for_project !== false),
  )

  async function refresh(projectId?: number): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const pluginQuery = projectId ? `?project_id=${projectId}` : ''
      const [pluginRows, catalogBody, capabilityRows, providerRows, actionRows, resourceRows] =
        await Promise.all([
          apiFetch<SchemaPluginOut[]>(`/api/v1/plugins${pluginQuery}`),
          apiFetch<SchemaCatalogOut>(`/api/v1/catalog${pluginQuery}`),
          apiFetch<SchemaCapabilityOut[]>(`/api/v1/capabilities${pluginQuery}`),
          apiFetch<SchemaProviderOut[]>(`/api/v1/providers${pluginQuery}`),
          apiFetch<SchemaActionOut[]>(`/api/v1/actions${pluginQuery}`),
          apiFetch<SchemaResourceOut[]>(`/api/v1/resources${pluginQuery}`),
        ])
      plugins.value = pluginRows
      catalog.value = catalogBody
      capabilities.value = capabilityRows
      providers.value = providerRows
      actions.value = actionRows
      resources.value = resourceRows
    } catch (err) {
      error.value = formatApiError(err, 'failed to load StackOS catalog')
    } finally {
      loading.value = false
    }
  }

  async function refreshAuth(projectId: number): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const [providerRows, status] = await Promise.all([
        apiFetch<SchemaAuthProviderOut[]>('/api/v1/auth/providers'),
        apiFetch<SchemaAuthStatusOut>(`/api/v1/projects/${projectId}/auth/status`),
      ])
      authProviders.value = providerRows
      authStatus.value = status
    } catch (err) {
      error.value = formatApiError(err, 'failed to load connections')
    } finally {
      loading.value = false
    }
  }

  async function storeCredential(
    projectId: number,
    providerKey: string,
    body: SchemaAuthCredentialSetRequest,
  ): Promise<SchemaWriteResponseAuthCredentialSetOut> {
    error.value = null
    const response = await apiFetch<SchemaWriteResponseAuthCredentialSetOut>(
      `/api/v1/projects/${projectId}/auth/${providerKey}/credentials`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
    )
    await refreshAuth(projectId)
    return response
  }

  async function startCredential(
    projectId: number,
    providerKey: string,
    body: SchemaAuthStartRequest,
  ): Promise<SchemaWriteResponseAuthStartOut> {
    error.value = null
    const response = await apiFetch<SchemaWriteResponseAuthStartOut>(
      `/api/v1/projects/${projectId}/auth/${providerKey}/start`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
    )
    await refreshAuth(projectId)
    return response
  }

  async function testCredential(
    projectId: number,
    body: SchemaAuthTestRequest,
  ): Promise<SchemaWriteResponseAuthTestOut> {
    error.value = null
    const response = await apiFetch<SchemaWriteResponseAuthTestOut>(
      `/api/v1/projects/${projectId}/auth/test`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
    )
    await refreshAuth(projectId)
    return response
  }

  async function revokeCredential(
    projectId: number,
    body: SchemaAuthRevokeRequest,
  ): Promise<SchemaWriteResponseAuthRevokeOut> {
    error.value = null
    const response = await apiFetch<SchemaWriteResponseAuthRevokeOut>(
      `/api/v1/projects/${projectId}/auth/revoke`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
    )
    await refreshAuth(projectId)
    return response
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
    authStatus,
    actions,
    resources,
    loading,
    error,
    enabledPlugins,
    refresh,
    refreshAuth,
    storeCredential,
    startCredential,
    testCredential,
    revokeCredential,
    actionsFor,
    capabilitiesFor,
    providersFor,
    resourcesFor,
  }
})
