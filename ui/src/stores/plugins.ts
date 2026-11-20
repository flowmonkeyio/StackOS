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
import { ApiError, apiFetch, formatApiError } from '@/lib/client'
import { createProjectRequestGate } from '@/lib/stackos/projectRequestGate'
import { callOperation } from '@/lib/operations'
import { isProjectPluginEnabled } from '@/lib/stackos/pluginPage'

/** Safe projection of one project-authorized Telegram Account session. */
export type TelegramAccountSessionStatus = SchemaAccountSessionOut

export const useStackOsCatalogStore = defineStore('stackosCatalog', () => {
  const plugins = ref<SchemaPluginOut[]>([])
  const capabilities = ref<SchemaCapabilityOut[]>([])
  const providers = ref<SchemaProviderOut[]>([])
  const authProviders = ref<SchemaAuthProviderOut[]>([])
  const globalAccountsStatus = ref<SchemaAuthStatusOut | null>(null)
  const homeAccounts = ref<SchemaAccountOut[]>([])
  const homeAccountsLoaded = ref(false)
  const homeAccountsLoading = ref(false)
  const homeAccountsError = ref<string | null>(null)
  let homeAccountsRead: Promise<void> | null = null
  function refreshHomeAccounts(): Promise<void> {
    if (homeAccountsRead) return homeAccountsRead
    homeAccountsLoading.value = true
    homeAccountsRead = apiFetch<SchemaAuthStatusOut>('/api/v1/auth/accounts?summary_only=true').then(status => {
      homeAccounts.value = status.accounts
      homeAccountsLoaded.value = true
      homeAccountsError.value = null
    }).catch(cause => {
      if (cause instanceof ApiError && [401, 403, 404].includes(cause.status)) {
        homeAccounts.value = []
        homeAccountsLoaded.value = false
      }
      homeAccountsError.value = formatApiError(cause, 'Could not refresh connected tools.')
    }).finally(() => { homeAccountsRead = null; homeAccountsLoading.value = false })
    return homeAccountsRead
  }
  const authStatus = ref<SchemaAuthStatusOut | null>(null)
  const accountsLoading = ref(false)
  const accountsError = ref<string | null>(null)
  const authLoading = ref(false)
  const authError = ref<string | null>(null)
  const actions = ref<SchemaActionListItemOut[]>([])
  const catalogLoading = ref(false)
  const catalogLoaded = ref(false)
  const catalogError = ref<string | null>(null)
  const pluginError = ref<string | null>(null)
  const pluginErrorStatus = ref<number | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const pluginProjectId = ref<number | null>(null)
  const connectionProjectId = ref<number | null>(null)
  let accountRefreshSequence = 0
  const authRequests = createProjectRequestGate(() => {
    authStatus.value = null
    connectionProjectId.value = null
    authLoading.value = false
    authError.value = null
  })
  let pendingPlugins: { projectId: number; promise: Promise<boolean> } | null = null
  function clearCatalog(): void {
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
    loading.value = false
    catalogError.value = null
    pluginError.value = null
    pluginErrorStatus.value = null
  }
  const projectRequests = createProjectRequestGate(clearCatalog)

  function invalidateDeniedCatalog(err: unknown): void {
    if (!(err instanceof ApiError) || ![401, 403, 404].includes(err.status)) return
    // Retire all reads before clearing rows, including sibling inventory reads.
    // Accounts and project auth have separate owners and lifetimes.
    projectRequests.invalidate()
    clearCatalog()
    error.value = formatApiError(err, 'failed to load StackOS plugins')
    pluginError.value = error.value
    pluginErrorStatus.value = err.status
    catalogError.value = error.value
  }

  const enabledPlugins = computed(() =>
    plugins.value.filter(isProjectPluginEnabled),
  )

  function refreshPlugins(
    projectId: number,
    options: { silent?: boolean; force?: boolean } = {},
  ): Promise<boolean> {
    // The shell and a domain detail can mount together. Share their read so
    // neither consumer cancels the other's project discovery.
    if (pendingPlugins?.projectId === projectId && !options.force) return pendingPlugins.promise
    // A completed setup mutation supersedes both writers of plugin readiness.
    // readPlugins begins a new compact-read generation; also retire any older
    // full-catalog read so it cannot restore pre-mutation plugin rows.
    if (options.force) {
      projectRequests.invalidateOperation('catalog')
      catalogLoading.value = false
    }
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
    pluginErrorStatus.value = null
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
        pluginErrorStatus.value = err instanceof ApiError ? err.status : null
        invalidateDeniedCatalog(err)
      }
      return false
    } finally {
      const current = request.isCurrent()
      request.finish()
      // A forced, silent replacement owns completion too. Retired reads cannot
      // leave the prior visible spinner running after current data has arrived.
      if (current) loading.value = false
    }
  }

  async function refresh(projectId: number): Promise<void> {
    const request = projectRequests.begin(projectId, 'catalog', { trackPending: false })
    // Promise.all reports only its first failure. Every current inventory denial
    // must still retire the scope after an earlier sibling transient failure.
    function observeDenial<T>(read: Promise<T>): Promise<T> {
      return read.catch(err => {
        if (request.isCurrent()) invalidateDeniedCatalog(err)
        throw err
      })
    }
    catalogLoading.value = true
    catalogError.value = null
    try {
      const pluginQuery = `?project_id=${projectId}`
      const [pluginsReady, capabilityRows, providerRows, actionInventory] =
        await Promise.all([
          refreshPlugins(projectId, { silent: true }),
          observeDenial(apiFetch<SchemaCapabilityOut[]>(`/api/v1/capabilities${pluginQuery}`)),
          observeDenial(apiFetch<SchemaProviderOut[]>(`/api/v1/providers${pluginQuery}`)),
          observeDenial(callOperation<SchemaActionListOut>('action.list', {
            project_id: projectId,
            include_unavailable_integrations: true,
          })),
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
    const request = authRequests.begin(projectId, 'connections', { trackPending: !options.silent })
    connectionProjectId.value = projectId
    authLoading.value = true
    try {
      const status = await apiFetch<SchemaAuthStatusOut>(
        `/api/v1/projects/${projectId}/connections/accounts`,
      )
      if (!request.isCurrent()) return
      authStatus.value = status
      connectionProjectId.value = projectId
      authError.value = null
    } catch (err) {
      if (request.isCurrent()) {
        authError.value = formatApiError(err, 'failed to load connections')
        if (err instanceof ApiError && [401, 403, 404].includes(err.status)) {
          authStatus.value = null
          authRequests.invalidate()
          authLoading.value = false
        }
      }
    } finally {
      if (request.isCurrent()) authLoading.value = false
      request.finish()
    }
  }

  async function refreshAccounts(options: { silent?: boolean } = {}): Promise<void> {
    const requestSequence = ++accountRefreshSequence
    void options
    accountsLoading.value = true
    try {
      const status = await apiFetch<SchemaAuthStatusOut>('/api/v1/auth/accounts')
      if (requestSequence !== accountRefreshSequence) return
      authProviders.value = status.providers
      globalAccountsStatus.value = status
      accountsError.value = null
    } catch (err) {
      if (requestSequence === accountRefreshSequence) {
        accountsError.value = formatApiError(err, 'failed to load accounts')
        if (err instanceof ApiError && [401, 403].includes(err.status)) {
          globalAccountsStatus.value = null
          authProviders.value = []
        }
      }
    } finally {
      if (requestSequence === accountRefreshSequence) accountsLoading.value = false
    }
  }

  function releaseAuth(projectId: number): void {
    // Only retire this consumer's project; a replacement scope may already own it.
    if (connectionProjectId.value !== projectId) return
    authRequests.invalidate()
    authStatus.value = null
    connectionProjectId.value = null
    authLoading.value = false
    authError.value = null
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
    accountRefreshSequence += 1
    authRequests.invalidateOperation('connections')
    authLoading.value = false
    for (const snapshot of [globalAccountsStatus.value, authStatus.value]) {
      const account = snapshot?.accounts.find(row => row.credential_ref === credentialRef)
      if (account) {
        account.status = response.data.status
        account.revoked_at = response.data.revoked_at
      }
    }
    await refreshAccounts({ silent: true })
    return response
  }

  async function attachAccount(
    projectId: number,
    credentialRef: string,
  ): Promise<SchemaAccountOut> {
    const isCurrentScope = authRequests.captureScope(projectId)
    error.value = null
    const response = await apiFetch<{ data: SchemaAccountOut }>(
      `/api/v1/projects/${projectId}/connections/accounts/${encodeURIComponent(credentialRef)}`,
      { method: 'POST' },
    )
    if (isCurrentScope()) authRequests.invalidateOperation('connections')
    if (isCurrentScope() && authStatus.value) {
      authStatus.value.accounts = [
        ...authStatus.value.accounts.filter(row => row.credential_ref !== credentialRef),
        response.data,
      ]
    }
    if (globalAccountsStatus.value) {
      globalAccountsStatus.value.accounts = globalAccountsStatus.value.accounts.map(row =>
        row.credential_ref === credentialRef ? response.data : row)
    }
    await refreshAccountReadbacks(projectId, isCurrentScope)
    return response.data
  }

  async function detachAccount(
    projectId: number,
    credentialRef: string,
  ): Promise<SchemaAccountOut> {
    const isCurrentScope = authRequests.captureScope(projectId)
    error.value = null
    const response = await apiFetch<{ data: SchemaAccountOut }>(
      `/api/v1/projects/${projectId}/connections/accounts/${encodeURIComponent(credentialRef)}`,
      { method: 'DELETE' },
    )
    if (isCurrentScope()) authRequests.invalidateOperation('connections')
    if (isCurrentScope() && authStatus.value) {
      authStatus.value.accounts = authStatus.value.accounts.filter(row => row.credential_ref !== credentialRef)
    }
    if (globalAccountsStatus.value) {
      globalAccountsStatus.value.accounts = globalAccountsStatus.value.accounts.map(row =>
        row.credential_ref === credentialRef ? response.data : row)
    }
    await refreshAccountReadbacks(projectId, isCurrentScope)
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
    const isCurrentScope = authRequests.captureScope(projectId)
    error.value = null
    const response = await apiFetch<SchemaWriteResponseAccountSessionOut>(
      `/api/v1/projects/${projectId}/connections/accounts/${encodeURIComponent(credentialRef)}/session/connect`,
      { method: 'POST' },
    )
    await refreshAccountReadbacks(projectId, isCurrentScope)
    return response.data
  }

  async function disconnectTelegramAccountSession(
    projectId: number,
    credentialRef: string,
  ): Promise<TelegramAccountSessionStatus> {
    const isCurrentScope = authRequests.captureScope(projectId)
    error.value = null
    const response = await apiFetch<SchemaWriteResponseAccountSessionOut>(
      `/api/v1/projects/${projectId}/connections/accounts/${encodeURIComponent(credentialRef)}/session/disconnect`,
      { method: 'POST' },
    )
    await refreshAccountReadbacks(projectId, isCurrentScope)
    return response.data
  }

  async function refreshAccountReadbacks(projectId: number, isCurrentScope: () => boolean): Promise<void> {
    // The Account mutation is confirmed globally, but its originating route may
    // have exited (including A -> B -> A) while the write was in flight.
    await Promise.all([
      refreshAccounts({ silent: true }),
      ...(isCurrentScope() ? [refreshAuth(projectId, { silent: true })] : []),
    ])
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
    homeAccounts, homeAccountsLoaded, homeAccountsLoading, homeAccountsError, refreshHomeAccounts,
    authStatus,
    accountsLoading,
    accountsError,
    authLoading,
    authError,
    releaseAuth,
    actions,
    catalogLoading,
    catalogLoaded,
    catalogError,
    pluginError,
    pluginErrorStatus,
    loading,
    error,
    pluginProjectId,
    connectionProjectId,
    enabledPlugins,
    refreshPlugins,
    capturePluginScope: projectRequests.captureScope,
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
