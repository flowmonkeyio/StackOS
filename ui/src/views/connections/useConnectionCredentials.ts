import { computed, ref, type ComputedRef } from 'vue'
import { storeToRefs } from 'pinia'

import type { SchemaAuthProviderOut } from '@/api'
import { formatApiError } from '@/lib/client'
import { useStackOsCatalogStore } from '@/stores/plugins'
import {
  compareConnections,
  connectionNeedsAttention,
  connectionStatusKey,
  serviceName,
} from './formatters'
import type { ConnectionRow, MessageMap, ServiceGroup } from './types'

export function useConnectionCredentials(projectId: ComputedRef<number>) {
  const catalogStore = useStackOsCatalogStore()
  const {
    authProviders,
    globalAccountsStatus,
    authStatus,
    loading,
    error,
  } = storeToRefs(catalogStore)

  const attachPanelOpen = ref(false)
  const selectedAccountRef = ref('')
  const providerFilter = ref('')
  const busyAction = ref<string | null>(null)
  const connectionMessages = ref<MessageMap>({})
  const pendingDetach = ref<ConnectionRow | null>(null)

  const connections = computed<ConnectionRow[]>(() =>
    (authStatus.value?.accounts ?? []).map((account) => ({
      ...account,
      id: account.credential_ref,
      project_ids: account.project_ids ?? [],
    })),
  )
  const allAccounts = computed<ConnectionRow[]>(() =>
    (globalAccountsStatus.value?.accounts ?? []).map((account) => ({
      ...account,
      id: account.credential_ref,
      project_ids: account.project_ids ?? [],
    })),
  )
  const providerByKey = computed(() => {
    const rows = new Map<string, SchemaAuthProviderOut>()
    for (const provider of authProviders.value) rows.set(provider.key, provider)
    return rows
  })
  const attachedAccountRefs = computed(
    () => new Set(connections.value.map((connection) => connection.credential_ref)),
  )
  const availableAccounts = computed(() =>
    allAccounts.value.filter(
      (account) =>
        // An Account may be attached alongside any number of Accounts from the
        // same provider. The only duplicate we exclude is this exact Account.
        !attachedAccountRefs.value.has(account.credential_ref) &&
        !account.project_ids.includes(projectId.value) &&
        (!providerFilter.value || account.provider_key === providerFilter.value) &&
        account.revoked_at === null &&
        account.status !== 'revoked',
    ),
  )
  const accountOptions = computed(() =>
    availableAccounts.value.map((account) => ({
      value: account.credential_ref,
      label: account.display_name,
      group: providerByKey.value.get(account.provider_key)?.name ?? account.provider_key,
    })),
  )
  const activeConnections = computed(() =>
    connections.value.filter((connection) => connection.revoked_at === null),
  )
  const connectedConnections = computed(() =>
    activeConnections.value.filter((connection) => connectionStatusKey(connection) === 'connected'),
  )
  const attentionConnections = computed(() =>
    activeConnections.value.filter(connectionNeedsAttention),
  )
  const serviceGroups = computed<ServiceGroup[]>(() => {
    const grouped = new Map<string, ConnectionRow[]>()
    for (const connection of activeConnections.value) {
      const rows = grouped.get(connection.provider_key) ?? []
      rows.push(connection)
      grouped.set(connection.provider_key, rows)
    }
    return Array.from(grouped.entries())
      .map(([providerKey, rows]) => ({
        providerKey,
        provider: providerByKey.value.get(providerKey) ?? null,
        connections: [...rows].sort(compareConnections),
      }))
      .sort((left, right) => {
        const attentionDiff =
          Number(!left.connections.some(connectionNeedsAttention)) -
          Number(!right.connections.some(connectionNeedsAttention))
        if (attentionDiff !== 0) return attentionDiff
        return serviceName(left).localeCompare(serviceName(right))
      })
  })
  const connectedServiceCount = computed(
    () => new Set(connectedConnections.value.map((connection) => connection.provider_key)).size,
  )

  async function load(): Promise<void> {
    await Promise.all([
      catalogStore.refreshAccounts(),
      catalogStore.refreshAuth(projectId.value),
    ])
  }

  function selectAccount(credentialRef: string): void {
    // Attach failures stay in the panel until the operator changes their
    // selection. A previous attempt on a different Account must not reappear
    // when they come back to it later in this panel session.
    if (connectionMessages.value[credentialRef]) {
      const nextMessages = { ...connectionMessages.value }
      delete nextMessages[credentialRef]
      connectionMessages.value = nextMessages
    }
    selectedAccountRef.value = credentialRef
  }

  function openAddConnection(providerKey?: string): void {
    providerFilter.value = providerKey ?? ''
    const first = availableAccounts.value[0]
    selectAccount(first?.credential_ref ?? '')
    attachPanelOpen.value = true
  }

  function closeAttachPanel(): void {
    attachPanelOpen.value = false
    providerFilter.value = ''
    selectedAccountRef.value = ''
  }

  async function attachSelectedAccount(): Promise<void> {
    const credentialRef = selectedAccountRef.value
    if (!credentialRef) return
    busyAction.value = `${credentialRef}:attach`
    try {
      await catalogStore.attachAccount(projectId.value, credentialRef)
      connectionMessages.value = {
        ...connectionMessages.value,
        [credentialRef]: { tone: 'success', text: 'Account attached to this project.' },
      }
      closeAttachPanel()
    } catch (err) {
      connectionMessages.value = {
        ...connectionMessages.value,
        [credentialRef]: {
          tone: 'danger',
          text: formatApiError(err, 'failed to attach Account'),
        },
      }
    } finally {
      busyAction.value = null
    }
  }

  function requestDetach(connection: ConnectionRow): void {
    pendingDetach.value = connection
  }

  async function confirmDetach(): Promise<void> {
    const connection = pendingDetach.value
    if (!connection) return
    busyAction.value = `${connection.credential_ref}:detach`
    try {
      await catalogStore.detachAccount(projectId.value, connection.credential_ref)
    } catch (err) {
      connectionMessages.value = {
        ...connectionMessages.value,
        [connection.credential_ref]: {
          tone: 'danger',
          text: formatApiError(err, 'failed to detach Account'),
        },
      }
    } finally {
      busyAction.value = null
      pendingDetach.value = null
    }
  }

  return {
    authStatus,
    loading,
    error,
    attachPanelOpen,
    selectedAccountRef,
    providerFilter,
    busyAction,
    connectionMessages,
    pendingDetach,
    connections,
    availableAccounts,
    accountOptions,
    activeConnections,
    connectedConnections,
    attentionConnections,
    serviceGroups,
    connectedServiceCount,
    load,
    openAddConnection,
    closeAttachPanel,
    selectAccount,
    attachSelectedAccount,
    requestDetach,
    confirmDetach,
  }
}
