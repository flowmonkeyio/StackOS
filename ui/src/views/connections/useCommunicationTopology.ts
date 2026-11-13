import { onBeforeUnmount, ref, type ComputedRef } from 'vue'

import { formatApiError } from '@/lib/client'
import { callOperation } from '@/lib/operations'

import type {
  CommunicationProfile,
  CommunicationProfileListOut,
  CommunicationRoute,
  CommunicationRouteListOut,
  CommunicationSurface,
  CommunicationSurfaceListOut,
  CommunicationTarget,
  CommunicationTargetListOut,
  MessageTone,
} from './types'

export function useCommunicationTopology(projectId: ComputedRef<number>) {
  const profiles = ref<CommunicationProfile[]>([])
  const targets = ref<CommunicationTarget[]>([])
  const surfaces = ref<CommunicationSurface[]>([])
  const routes = ref<CommunicationRoute[]>([])
  const loading = ref(false)
  const loaded = ref(false)
  const message = ref<{ tone: MessageTone; text: string } | null>(null)
  let requestSequence = 0
  let scope: number | null = null
  let retired = false
  onBeforeUnmount(() => { retired = true; requestSequence += 1 })

  async function load(): Promise<void> {
    if (retired || !projectId.value || Number.isNaN(projectId.value)) return
    const currentRequest = ++requestSequence
    const requestedProjectId = projectId.value
    loading.value = true
    if (scope !== requestedProjectId) {
      scope = requestedProjectId
      profiles.value = []
      targets.value = []
      surfaces.value = []
      routes.value = []
      loaded.value = false
      message.value = null
    }
    try {
      const results = await Promise.allSettled([
        callOperation<CommunicationProfileListOut>('communicationProfile.list', {
          project_id: requestedProjectId,
          limit: 50,
        }),
        callOperation<CommunicationTargetListOut>('communicationTarget.list', {
          project_id: requestedProjectId,
          limit: 50,
        }),
        callOperation<CommunicationSurfaceListOut>('communicationSurface.list', {
          project_id: requestedProjectId,
          limit: 50,
        }),
        callOperation<CommunicationRouteListOut>('communicationRoute.list', {
          project_id: requestedProjectId,
          limit: 50,
        }),
      ])
      if (currentRequest !== requestSequence || requestedProjectId !== projectId.value) return
      const [profileRows, targetRows, surfaceRows, routeRows] = results
      if (profileRows.status === 'fulfilled') profiles.value = profileRows.value.items ?? []
      if (targetRows.status === 'fulfilled') targets.value = targetRows.value.items ?? []
      if (surfaceRows.status === 'fulfilled') surfaces.value = surfaceRows.value.items ?? []
      if (routeRows.status === 'fulfilled') routes.value = routeRows.value.items ?? []
      loaded.value = results.every(result => result.status === 'fulfilled') || loaded.value
      const failed = results.filter(result => result.status === 'rejected')
      message.value = failed.length ? {
        tone: 'danger', text: failed.map(result => formatApiError(result.reason, 'failed to load communication setup')).join('; '),
      } : null
    } catch (err) {
      if (currentRequest === requestSequence) {
        message.value = {
          tone: 'danger',
          text: formatApiError(err, 'failed to load communication setup'),
        }
      }
    } finally {
      if (currentRequest === requestSequence) loading.value = false
    }
  }

  return { profiles, targets, surfaces, routes, loading, loaded, message, load }
}
