import { computed, onBeforeUnmount, ref, type ComputedRef, type Ref } from 'vue'

import { ApiError, formatApiError } from '@/lib/client'
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
  IngressEndpointRoute,
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
  const mutationMessage = ref<{ tone: MessageTone; text: string } | null>(null)
  const message = computed(() => {
    const failure = loadMessage.value
    const outcome = mutationMessage.value
    return failure && outcome
      ? { tone: failure.tone, text: `${outcome.text} ${failure.text}` }
      : failure ?? outcome
  })
  const form = ref<IngressForm>({
    driver: 'local-tunnel',
    public_base_url: '',
    discovery_url: 'http://127.0.0.1:4040/api/endpoints',
  })
  let loadRequestSequence = 0
  let loadScope: number | null = null
  let authorityGeneration = 0
  let mutationSequence = 0
  let retired = false
  onBeforeUnmount(() => { retired = true; authorityGeneration += 1; loadRequestSequence += 1 })

  const manualRoutes = computed(() =>
    (status.value?.routes ?? []).filter(routeNeedsManualProviderUpdate),
  )

  async function load(): Promise<void> {
    if (retired || !options.projectId.value || Number.isNaN(options.projectId.value)) return
    const requestedProjectId = prepareScope()
    const requestSequence = ++loadRequestSequence
    loading.value = true
    try {
      const ingress = await callOperation<IngressEndpointStatusOut>('ingressEndpoint.status', {
        project_id: requestedProjectId,
      })
      if (requestSequence !== loadRequestSequence || requestedProjectId !== options.projectId.value)
        return
      status.value = applyProviderResultsToIngressStatus(ingress ?? null, lastProviderResults.value)
      loadMessage.value = null
    } catch (err) {
      if (requestSequence === loadRequestSequence && requestedProjectId === options.projectId.value) {
        if (err instanceof ApiError && [401, 403, 404].includes(err.status)) {
          const outcome = mutationMessage.value
          reset()
          mutationMessage.value = outcome
        }
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
    mutationMessage.value = null
    panelOpen.value = true
  }

  function reset(): void {
    authorityGeneration += 1
    loadRequestSequence += 1
    status.value = null
    lastProviderResults.value = []
    loading.value = false
    loadMessage.value = null
    panelOpen.value = false
    mutationMessage.value = null
  }

  function prepareScope(): number {
    const projectId = options.projectId.value
    if (loadScope !== projectId) {
      reset()
      loadScope = projectId
    }
    return projectId
  }

  function beginMutation(action: string) {
    const projectId = prepareScope()
    const generation = authorityGeneration
    const sequence = ++mutationSequence
    options.busyAction.value = action
    return {
      projectId,
      isCurrent: () => !retired && projectId === options.projectId.value &&
        generation === authorityGeneration && sequence === mutationSequence,
      finish: () => {
        if (sequence === mutationSequence && options.busyAction.value === action)
          options.busyAction.value = null
      },
    }
  }

  async function save(): Promise<void> {
    if (retired) return
    const values = form.value
    if (values.driver === 'public-url' && !values.public_base_url.trim()) {
      mutationMessage.value = { tone: 'danger', text: 'A public address is required.' }
      return
    }
    const discoveryUrl = values.discovery_url.trim() || 'http://127.0.0.1:4040/api/endpoints'
    const mutation = beginMutation('ingress:configure')
    try {
      const configured = await callOperation<OperationWriteEnvelope<IngressEndpointOut>>(
        'ingressEndpoint.configure',
        {
          project_id: mutation.projectId,
          driver: values.driver,
          enabled: true,
          ...(values.driver === 'public-url'
            ? { public_base_url: values.public_base_url.trim() }
            : { driver_config: { provider: 'ngrok', discovery_url: discoveryUrl } }),
        },
      )
      if (!mutation.isCurrent()) return
      let endpoint = configured.data
      if (values.driver === 'local-tunnel') {
        const refreshed = await callOperation<OperationWriteEnvelope<IngressEndpointSyncOut>>(
          'ingressEndpoint.refresh',
          {
            project_id: mutation.projectId,
            driver_config: { provider: 'ngrok', discovery_url: discoveryUrl },
            sync_profiles: true,
          },
        )
        if (!mutation.isCurrent()) return
        endpoint = refreshed.data.endpoint
      }
      const discoveryError = discoveryFailureMessage(endpoint)
      if (values.driver === 'local-tunnel' && discoveryError) {
        mutationMessage.value = { tone: 'danger', text: discoveryError }
        await reloadAfterMutation()
        return
      }
      if (values.driver === 'public-url' && !endpointHasPublicAddress(endpoint)) {
        mutationMessage.value = {
          tone: 'danger',
          text: 'Connectivity was saved, but no public address is configured.',
        }
        await reloadAfterMutation()
        return
      }
      lastProviderResults.value = []
      mutationMessage.value = { tone: 'success', text: 'Connectivity configured.' }
      panelOpen.value = false
      await reloadAfterMutation()
    } catch (err) {
      if (!mutation.isCurrent()) return
      mutationMessage.value = {
        tone: 'danger',
        text: formatApiError(err, 'failed to configure connectivity'),
      }
    } finally {
      mutation.finish()
    }
  }

  async function sync(manualRoute?: IngressEndpointRoute): Promise<void> {
    if (retired) return
    const mutation = beginMutation('ingress:sync')
    try {
      if (manualRoute?.ingress_url) {
        const confirmed = await callOperation<OperationWriteEnvelope<IngressEndpointStatusOut>>(
          'ingressEndpoint.confirmManualUpdate',
          {
            project_id: mutation.projectId,
            provider_key: manualRoute.provider_key,
            profile_key: manualRoute.profile_key,
            ingress_url: manualRoute.ingress_url,
            response_mode: 'raw',
          },
        )
        if (!mutation.isCurrent()) return
        loadRequestSequence += 1
        loading.value = false
        status.value = confirmed.data
        lastProviderResults.value = []
        mutationMessage.value = {
          tone: 'success',
          text: 'Slack webhook update confirmed for this route.',
        }
        await options.onTopologyChanged()
        return
      }
      const synced = await callOperation<OperationWriteEnvelope<IngressEndpointSyncOut>>(
        'ingressEndpoint.sync',
        {
          project_id: mutation.projectId,
        },
      )
      if (!mutation.isCurrent()) return
      lastProviderResults.value = synced.data.provider_results ?? []
      mutationMessage.value = summarizeProviderResults(lastProviderResults.value)
      await reloadAfterMutation()
    } catch (err) {
      if (!mutation.isCurrent()) return
      mutationMessage.value = {
        tone: 'danger',
        text: formatApiError(err, 'failed to refresh provider routes'),
      }
    } finally {
      mutation.finish()
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
