import { computed, onBeforeUnmount, ref, type ComputedRef } from 'vue'

import { ApiError, formatApiError } from '@/lib/client'
import { callOperation } from '@/lib/operations'
import { createProjectRequestGate } from '@/lib/stackos/projectRequestGate'

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

type TopologySource = 'profiles' | 'targets' | 'surfaces' | 'routes'
interface TopologyReadState {
  loading: boolean
  loaded: boolean
  message: { tone: MessageTone; text: string } | null
}
const sources: TopologySource[] = ['profiles', 'targets', 'surfaces', 'routes']
function initialStates(): Record<TopologySource, TopologyReadState> {
  return {
    profiles: { loading: false, loaded: false, message: null },
    targets: { loading: false, loaded: false, message: null },
    surfaces: { loading: false, loaded: false, message: null },
    routes: { loading: false, loaded: false, message: null },
  }
}

export function useCommunicationTopology(projectId: ComputedRef<number>) {
  const profiles = ref<CommunicationProfile[]>([])
  const targets = ref<CommunicationTarget[]>([])
  const surfaces = ref<CommunicationSurface[]>([])
  const routes = ref<CommunicationRoute[]>([])
  const states = ref(initialStates())
  const loading = computed(() => sources.some(key => states.value[key].loading))
  const loaded = computed(() => sources.every(key => states.value[key].loaded))
  const message = computed(() => {
    const errors = sources.flatMap(key => states.value[key].message?.text ?? [])
    return errors.length ? { tone: 'danger' as const, text: errors.join('; ') } : null
  })
  let retired = false
  const requests = createProjectRequestGate(() => {
    profiles.value = []
    targets.value = []
    surfaces.value = []
    routes.value = []
    states.value = initialStates()
  })
  onBeforeUnmount(() => { retired = true; requests.invalidate() })

  async function load(source?: TopologySource): Promise<void> {
    if (retired || !projectId.value || Number.isNaN(projectId.value)) return
    const requestedProjectId = projectId.value

    async function read<T>(key: TopologySource, operation: string, accept: (data: T) => void, clear: () => void) {
      const request = requests.begin(requestedProjectId, key, { trackPending: false })
      const isCurrent = () => request.isCurrent() && requestedProjectId === projectId.value
      states.value[key].loading = true
      try {
        const data = await callOperation<T>(operation, { project_id: requestedProjectId, limit: 50 })
        if (!isCurrent()) return
        accept(data)
        states.value[key].loaded = true
        states.value[key].message = null
      } catch (err) {
        if (!isCurrent()) return
        if (err instanceof ApiError && [401, 403, 404].includes(err.status)) {
          clear()
          states.value[key].loaded = false
        }
        states.value[key].message = { tone: 'danger', text: formatApiError(err, 'failed to load communication setup') }
      } finally {
        if (isCurrent()) states.value[key].loading = false
        request.finish()
      }
    }

    const reads = {
      profiles: () => read<CommunicationProfileListOut>('profiles', 'communicationProfile.list', data => { profiles.value = data.items ?? [] }, () => { profiles.value = [] }),
      targets: () => read<CommunicationTargetListOut>('targets', 'communicationTarget.list', data => { targets.value = data.items ?? [] }, () => { targets.value = [] }),
      surfaces: () => read<CommunicationSurfaceListOut>('surfaces', 'communicationSurface.list', data => { surfaces.value = data.items ?? [] }, () => { surfaces.value = [] }),
      routes: () => read<CommunicationRouteListOut>('routes', 'communicationRoute.list', data => { routes.value = data.items ?? [] }, () => { routes.value = [] }),
    }
    await Promise.all((source ? [source] : sources).map(key => reads[key]()))
  }

  return { profiles, targets, surfaces, routes, states, loading, loaded, message, load }
}
