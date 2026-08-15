import { computed } from 'vue'
import { useRoute, type RouteLocationNormalizedLoaded } from 'vue-router'

import { projectIdFromRoute } from '@/lib/stackos/projectNavigation'

type ProjectScopeRoute = Pick<RouteLocationNormalizedLoaded, 'path' | 'params'>

function isProjectPath(path: string): boolean {
  return path.startsWith('/projects/')
}

export function useProjectRouteScope(route: ProjectScopeRoute = useRoute()) {
  const isProjectRoute = computed(() => isProjectPath(route.path))
  const routeProjectId = computed(() => projectIdFromRoute(route))
  const projectId = computed(() => routeProjectId.value ?? Number.NaN)
  const isValid = computed(
    () => isProjectRoute.value && Number.isSafeInteger(projectId.value),
  )
  const scopeKey = computed(() =>
    isProjectRoute.value ? `project:${routeProjectId.value ?? 'invalid'}` : route.path,
  )

  function changesProjectScope(to: ProjectScopeRoute): boolean {
    if (!isProjectPath(to.path)) return isProjectRoute.value
    return projectIdFromRoute(to) !== routeProjectId.value
  }

  return {
    isProjectRoute,
    routeProjectId,
    projectId,
    isValid,
    scopeKey,
    changesProjectScope,
  }
}
