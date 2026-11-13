import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import type {
  SchemaActionListItemOut,
  SchemaActionListOut,
  SchemaAccountAuthStatusOut,
  SchemaAccountOut,
  SchemaAccountSessionOut,
  SchemaAuthAuthorizationSubmitRequest,
  SchemaAuthCredentialEditOut,
  SchemaAuthCredentialSetRequest,
  SchemaAuthCredentialUpdateRequest,
  SchemaAuthProviderOut,
  SchemaAuthStartRequest,
  SchemaAuthStatusOut,
  SchemaCapabilityOut,
  SchemaPluginOut,
  SchemaProviderOut,
  SchemaWriteResponseAuthCredentialSetOut,
  SchemaWriteResponseAuthRevokeOut,
  SchemaWriteResponseAuthStartOut,
  SchemaWriteResponseAuthTestOut,
  SchemaWriteResponseAccountAuthStatusOut,
  SchemaWriteResponseAccountSessionOut,
} from '@/api'
import { apiFetch, formatApiError } from '@/lib/client'
import { createProjectRequestGate } from '@/lib/stackos/projectRequestGate'
import { callOperation } from '@/lib/operations'

/** Safe projection of one project-authorized Telegram Account session. */
export type TelegramAccountSessionStatus = SchemaAccountSessionOut

export const useStackOsCatalogStore = defineStore('stackosCatalog', () => {
  const plugins = ref<SchemaPluginOut[]>([])
  const capabilities = ref<SchemaCapabilityOut[]>([])
  const providers = ref<SchemaProviderOut[]>([])
  const authProviders = ref<SchemaAuthProviderOut[]>([])
  const globalAccountsStatus = ref<SchemaAuthStatusOut | null>(null)
  const authStatus = ref<SchemaAuthStatusOut | null>(null)
  const actions = ref<SchemaActionListItemOut[]>([])
  const catalogLoading = ref(false)
  const catalogLoaded = ref(false)
  const catalogError = ref<string | null>(null)
  const pluginError = ref<string | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const pluginProjectId = ref<number | null>(null)
  const connectionProjectId = ref<number | null>(null)
  let accountRefreshSequence = 0
  let pendingPlugins: { projectId: number; promise: Promise<boolean> } | null = null
  const projectRequests = createProjectRequestGate(() => {
    // Incompatible project metadata must disappear immediately. Compatible
    // refreshes keep the same plugin rows and sidebar nodes mounted.
    pendingPlugins = null
    plugins.value = []
    pluginProjectId.value = null
    capabilities.value = []
    providers.value = []
    actions.value = []
    catalogLoaded.value = false
    catalogLoading.value = false
    catalogError.value = null
    pluginError.value = null
  })

  const enabledPlugins = computed(() =>
    plugins.value.filter((plugin) => plugin.enabled_for_project !== false),
  )

  function refreshPlugins(
    projectId: number,
    options: { silent?: boolean } = {},
  ): Promise<boolean> {
    // The shell and a domain detail can mount together. Share their read so
    // neither consumer cancels the other's project discovery.
    if (pendingPlugins?.projectId === projectId) return pendingPlugins.promise
    const promise = readPlugins(projectId, options)
    pendingPlugins = { projectId, promise }
    void promise.finally(() => {
      if (pendingPlugins?.promise === promise) pendingPlugins = null
    })
    return promise
  }

  async function readPlugins(
    projectId: number,
    options: { silent?: boolean } = {},
  ): Promise<boolean> {
    const request = projectRequests.begin(projectId, 'plugins', {
      trackPending: !options.silent,
    })
    if (!options.silent) loading.value = true
    error.value = null
    pluginError.value = null
    try {
      const pluginQuery = `?project_id=${projectId}`
      const compactQuery = `${pluginQuery}&compact=true`
      const pluginRows = await apiFetch<SchemaPluginOut[]>(`/api/v1/plugins${compactQuery}`)
      if (!request.isCurrent()) return false
      plugins.value = pluginRows
      pluginProjectId.value = projectId
      return true
    } catch (err) {
      if (request.isCurrent()) {
        error.value = formatApiError(err, 'failed to load StackOS plugins')
        pluginError.value = error.value
      }
      return false
    } finally {
      const hasPending = request.finish()
      if (!options.silent) loading.value = hasPending
    }
  }

  async function refresh(projectId: number): Promise<void> {
    const request = projectRequests.begin(projectId, 'catalog', { trackPending: false })
    catalogLoading.value = true
    catalogError.value = null
    try {
      const pluginQuery = `?project_id=${projectId}`
      const [pluginsReady, capabilityRows, providerRows, actionInventory] =
        await Promise.all([
          refreshPlugins(projectId, { silent: true }),
          apiFetch<SchemaCapabilityOut[]>(`/api/v1/capabilities${pluginQuery}`),
          apiFetch<SchemaProviderOut[]>(`/api/v1/providers${pluginQuery}`),
          callOperation<SchemaActionListOut>('action.list', {
            project_id: projectId,
            include_unavailable_integrations: true,
          }),
        ])
      if (!request.isCurrent()) return
      if (!pluginsReady) throw new Error(pluginError.value ?? 'failed to load StackOS plugins')
      if (!Array.isArray(actionInventory.items) || actionInventory.count !== actionInventory.items.length) {
        throw new Error('Incomplete StackOS action inventory')
      }
      capabilities.value = capabilityRows
      providers.value = providerRows
      actions.value = actionInventory.items
      catalogLoaded.value = true
    } catch (err) {
      if (request.isCurrent()) {
        catalogError.value = formatApiError(err, 'failed to load StackOS catalog')
      }
    } finally {
      if (request.isCurrent()) catalogLoading.value = false
      request.finish()
    }
  }

  async function refreshAuth(projectId: number, options: { silent?: boolean } = {}): Promise<void> {
    const request = projectRequests.begin(projectId, 'connections', {
      trackPending: !options.silent,
    })
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
      if (!request.isCurrent()) return
      authProviders.value = nextAuthProviders
      authStatus.value = status
      connectionProjectId.value = projectId
    } catch (err) {
      if (request.isCurrent()) {
        error.value = formatApiError(err, 'failed to load connections')
      }
    } finally {
      const hasPending = request.finish()
      if (!options.silent) loading.value = hasPending
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

  async function getAccountAuthorization(
    credentialRef: string,
  ): Promise<SchemaAccountAuthStatusOut> {
    error.value = null
    return apiFetch<SchemaAccountAuthStatusOut>(
      `/api/v1/auth/accounts/${encodeURIComponent(credentialRef)}/authorization`,
    )
  }

  async function submitAccountAuthorization(
    credentialRef: string,
    body: SchemaAuthAuthorizationSubmitRequest,
  ): Promise<SchemaWriteResponseAccountAuthStatusOut> {
    error.value = null
    const response = await apiFetch<SchemaWriteResponseAccountAuthStatusOut>(
      `/api/v1/auth/accounts/${encodeURIComponent(credentialRef)}/authorization`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
    )
    await refreshAccounts({ silent: true })
    return response
  }

  async function cancelAccountAuthorization(
    credentialRef: string,
    generation: number,
  ): Promise<SchemaWriteResponseAccountAuthStatusOut> {
    error.value = null
    const response = await apiFetch<SchemaWriteResponseAccountAuthStatusOut>(
      `/api/v1/auth/accounts/${encodeURIComponent(credentialRef)}/authorization?generation=${generation}`,
      { method: 'DELETE' },
    )
    await refreshAccounts({ silent: true })
    return response
  }

  async function testCredential(credentialRef: string): Promise<SchemaWriteResponseAuthTestOut> {
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

  async function attachAccount(
    projectId: number,
    credentialRef: string,
  ): Promise<SchemaAccountOut> {
    error.value = null
    const response = await apiFetch<{ data: SchemaAccountOut }>(
      `/api/v1/projects/${projectId}/connections/accounts/${encodeURIComponent(credentialRef)}`,
      { method: 'POST' },
    )
    await Promise.all([refreshAccounts({ silent: true }), refreshAuth(projectId, { silent: true })])
    return response.data
  }

  async function detachAccount(
    projectId: number,
    credentialRef: string,
  ): Promise<SchemaAccountOut> {
    error.value = null
    const response = await apiFetch<{ data: SchemaAccountOut }>(
      `/api/v1/projects/${projectId}/connections/accounts/${encodeURIComponent(credentialRef)}`,
      { method: 'DELETE' },
    )
    await Promise.all([refreshAccounts({ silent: true }), refreshAuth(projectId, { silent: true })])
    return response.data
  }

  async function getTelegramAccountSession(
    projectId: number,
    credentialRef: string,
  ): Promise<TelegramAccountSessionStatus> {
    error.value = null
    return apiFetch<TelegramAccountSessionStatus>(
      `/api/v1/projects/${projectId}/connections/accounts/${encodeURIComponent(credentialRef)}/session`,
    )
  }

  async function connectTelegramAccountSession(
    projectId: number,
    credentialRef: string,
  ): Promise<TelegramAccountSessionStatus> {
    error.value = null
    const response = await apiFetch<SchemaWriteResponseAccountSessionOut>(
      `/api/v1/projects/${projectId}/connections/accounts/${encodeURIComponent(credentialRef)}/session/connect`,
      { method: 'POST' },
    )
    await Promise.all([
      refreshAccounts({ silent: true }),
      refreshAuth(projectId, { silent: true }),
    ])
    return response.data
  }

  async function disconnectTelegramAccountSession(
    projectId: number,
    credentialRef: string,
  ): Promise<TelegramAccountSessionStatus> {
    error.value = null
    const response = await apiFetch<SchemaWriteResponseAccountSessionOut>(
      `/api/v1/projects/${projectId}/connections/accounts/${encodeURIComponent(credentialRef)}/session/disconnect`,
      { method: 'POST' },
    )
    await Promise.all([
      refreshAccounts({ silent: true }),
      refreshAuth(projectId, { silent: true }),
    ])
    return response.data
  }

  function actionsFor(pluginSlug: string): SchemaActionListItemOut[] {
    return actions.value.filter((action) => action.plugin_slug === pluginSlug)
  }

  function capabilitiesFor(pluginSlug: string): SchemaCapabilityOut[] {
    return capabilities.value.filter((capability) => capability.plugin_slug === pluginSlug)
  }

  function providersFor(pluginSlug: string): SchemaProviderOut[] {
    return providers.value.filter((provider) => provider.plugin_slug === pluginSlug)
  }

  return {
    plugins,
    capabilities,
    providers,
    authProviders,
    globalAccountsStatus,
    authStatus,
    actions,
    catalogLoading,
    catalogLoaded,
    catalogError,
    loading,
    error,
    pluginProjectId,
    connectionProjectId,
    enabledPlugins,
    refreshPlugins,
    refresh,
    refreshAuth,
    refreshAccounts,
    storeCredential,
    getCredential,
    updateCredential,
    startCredential,
    getAccountAuthorization,
    submitAccountAuthorization,
    cancelAccountAuthorization,
    testCredential,
    revokeCredential,
    attachAccount,
    detachAccount,
    getTelegramAccountSession,
    connectTelegramAccountSession,
    disconnectTelegramAccountSession,
    actionsFor,
    capabilitiesFor,
    providersFor,
  }
})
