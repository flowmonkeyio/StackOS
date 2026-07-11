import { ref, type ComputedRef } from 'vue'

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
  const message = ref<{ tone: MessageTone; text: string } | null>(null)
  let requestSequence = 0

  async function load(): Promise<void> {
    if (!projectId.value || Number.isNaN(projectId.value)) return
    const currentRequest = ++requestSequence
    const requestedProjectId = projectId.value
    loading.value = true
    profiles.value = []
    targets.value = []
    surfaces.value = []
    routes.value = []
    try {
      const [profileRows, targetRows, surfaceRows, routeRows] = await Promise.all([
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
      profiles.value = profileRows.items ?? []
      targets.value = targetRows.items ?? []
      surfaces.value = surfaceRows.items ?? []
      routes.value = routeRows.items ?? []
      message.value = null
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

  return { profiles, targets, surfaces, routes, loading, message, load }
}
