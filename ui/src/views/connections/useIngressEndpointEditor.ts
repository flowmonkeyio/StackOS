import { computed, ref, type ComputedRef, type Ref } from 'vue'

import { formatApiError } from '@/lib/client'
import { callOperation } from '@/lib/operations'

import {
  applyProviderResultsToIngressStatus,
  discoveryFailureMessage,
  endpointHasPublicAddress,
  routeNeedsManualProviderUpdate,
  summarizeProviderResults,
} from './ingressResults'
import type {
  IngressEndpointOut,
  IngressEndpointStatusOut,
  IngressEndpointSyncOut,
  IngressForm,
  IngressProviderResult,
  MessageTone,
  OperationWriteEnvelope,
} from './types'

interface IngressEndpointEditorOptions {
  projectId: ComputedRef<number>
  busyAction: Ref<string | null>
  onTopologyChanged: () => Promise<void>
}

export function useIngressEndpointEditor(options: IngressEndpointEditorOptions) {
  const status = ref<IngressEndpointStatusOut | null>(null)
  const lastProviderResults = ref<IngressProviderResult[]>([])
  const loading = ref(false)
  const loadMessage = ref<{ tone: MessageTone; text: string } | null>(null)
  const panelOpen = ref(false)
  const message = ref<{ tone: MessageTone; text: string } | null>(null)
  const form = ref<IngressForm>({
    driver: 'local-tunnel',
    public_base_url: '',
    discovery_url: 'http://127.0.0.1:4040/api/endpoints',
  })
  let loadRequestSequence = 0

  const manualRoutes = computed(() =>
    (status.value?.routes ?? []).filter(routeNeedsManualProviderUpdate),
  )

  async function load(): Promise<void> {
    if (!options.projectId.value || Number.isNaN(options.projectId.value)) return
    const requestSequence = ++loadRequestSequence
    const requestedProjectId = options.projectId.value
    loading.value = true
    status.value = null
    try {
      const ingress = await callOperation<IngressEndpointStatusOut>('ingressEndpoint.status', {
        project_id: requestedProjectId,
      })
      if (requestSequence !== loadRequestSequence || requestedProjectId !== options.projectId.value)
        return
      status.value = applyProviderResultsToIngressStatus(ingress ?? null, lastProviderResults.value)
      loadMessage.value = null
    } catch (err) {
      if (requestSequence === loadRequestSequence) {
        loadMessage.value = {
          tone: 'danger',
          text: formatApiError(err, 'failed to load connectivity setup'),
        }
      }
    } finally {
      if (requestSequence === loadRequestSequence) loading.value = false
    }
  }

  function openPanel(): void {
    const endpoint = status.value?.endpoint
    form.value = {
      driver: endpoint?.driver === 'public-url' ? 'public-url' : 'local-tunnel',
      public_base_url: endpoint?.public_base_url ?? '',
      discovery_url: form.value.discovery_url || 'http://127.0.0.1:4040/api/endpoints',
    }
    message.value = null
    panelOpen.value = true
  }

  function reset(): void {
    loadRequestSequence += 1
    status.value = null
    lastProviderResults.value = []
    loading.value = false
    loadMessage.value = null
    panelOpen.value = false
    message.value = null
  }

  async function save(): Promise<void> {
    const values = form.value
    if (values.driver === 'public-url' && !values.public_base_url.trim()) {
      message.value = { tone: 'danger', text: 'A public address is required.' }
      return
    }
    const discoveryUrl = values.discovery_url.trim() || 'http://127.0.0.1:4040/api/endpoints'
    options.busyAction.value = 'ingress:configure'
    try {
      const configured = await callOperation<OperationWriteEnvelope<IngressEndpointOut>>(
        'ingressEndpoint.configure',
        {
          project_id: options.projectId.value,
          driver: values.driver,
          enabled: true,
          ...(values.driver === 'public-url'
            ? { public_base_url: values.public_base_url.trim() }
            : { driver_config: { provider: 'ngrok', discovery_url: discoveryUrl } }),
        },
      )
      let endpoint = configured.data
      if (values.driver === 'local-tunnel') {
        const refreshed = await callOperation<OperationWriteEnvelope<IngressEndpointSyncOut>>(
          'ingressEndpoint.refresh',
          {
            project_id: options.projectId.value,
            driver_config: { provider: 'ngrok', discovery_url: discoveryUrl },
            sync_profiles: true,
          },
        )
        endpoint = refreshed.data.endpoint
      }
      const discoveryError = discoveryFailureMessage(endpoint)
      if (values.driver === 'local-tunnel' && discoveryError) {
        message.value = { tone: 'danger', text: discoveryError }
        await reloadAfterMutation()
        return
      }
      if (values.driver === 'public-url' && !endpointHasPublicAddress(endpoint)) {
        message.value = {
          tone: 'danger',
          text: 'Connectivity was saved, but no public address is configured.',
        }
        await reloadAfterMutation()
        return
      }
      lastProviderResults.value = []
      message.value = { tone: 'success', text: 'Connectivity configured.' }
      panelOpen.value = false
      await reloadAfterMutation()
    } catch (err) {
      message.value = {
        tone: 'danger',
        text: formatApiError(err, 'failed to configure connectivity'),
      }
    } finally {
      options.busyAction.value = null
    }
  }

  async function sync(): Promise<void> {
    options.busyAction.value = 'ingress:sync'
    try {
      const synced = await callOperation<OperationWriteEnvelope<IngressEndpointSyncOut>>(
        'ingressEndpoint.sync',
        {
          project_id: options.projectId.value,
          apply_provider_webhooks: true,
          dry_run_provider_webhooks: false,
        },
      )
      lastProviderResults.value = synced.data.provider_results ?? []
      await reloadAfterMutation()
      message.value = summarizeProviderResults(lastProviderResults.value)
    } catch (err) {
      message.value = {
        tone: 'danger',
        text: formatApiError(err, 'failed to sync webhooks'),
      }
    } finally {
      options.busyAction.value = null
    }
  }

  async function reloadAfterMutation(): Promise<void> {
    await Promise.all([load(), options.onTopologyChanged()])
  }

  return {
    status,
    loading,
    loadMessage,
    panelOpen,
    message,
    form,
    manualRoutes,
    load,
    reset,
    openPanel,
    save,
    sync,
  }
}
