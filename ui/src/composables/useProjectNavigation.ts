import { computed, onBeforeUnmount, onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import { isNavigationFailure, useRoute, useRouter, type RouteLocationNormalized } from 'vue-router'

import {
  projectNavSections as buildProjectNavSections,
  type StackOsNavSection,
} from '@/lib/stackos/nav'
import {
  projectIdFromRoute,
  projectSwitchDestination,
} from '@/lib/stackos/projectNavigation'
import { useStackOsCatalogStore } from '@/stores/plugins'
import { useProjectsStore } from '@/stores/projects'
import { useProjectRouteScope } from '@/composables/useProjectRouteScope'

interface CatalogRefresh {
  projectId: number
  promise: Promise<boolean>
}

export function useProjectNavigation() {
  const route = useRoute()
  const router = useRouter()
  const projects = useProjectsStore()
  const catalog = useStackOsCatalogStore()
  const { items: projectItems, activeProject } = storeToRefs(projects)
  const { enabledPlugins, pluginProjectId } = storeToRefs(catalog)
  let catalogRefresh: CatalogRefresh | null = null

  const { routeProjectId, isProjectRoute, scopeKey: routeViewKey } =
    useProjectRouteScope(route)
  const navigationProjectId = computed(() => {
    if (isProjectRoute.value) return routeProjectId.value
    return activeProject.value?.id ?? null
  })
  const currentProject = computed(() => {
    if (isProjectRoute.value) {
      return routeProjectId.value === null ? null : projects.getById(routeProjectId.value)
    }
    return activeProject.value
  })
  const projectNavSections = computed<StackOsNavSection[]>(() => {
    const projectId = navigationProjectId.value
    if (projectId === null) return []
    const scopedPlugins =
      pluginProjectId.value === projectId ? enabledPlugins.value : []
    return buildProjectNavSections(projectId, scopedPlugins)
  })
  async function refreshNavigationCatalog(projectId: number): Promise<boolean> {
    if (pluginProjectId.value === projectId) return true
    if (catalogRefresh?.projectId === projectId) return catalogRefresh.promise

    const promise = catalog.refreshPlugins(projectId, { silent: true })
    const request: CatalogRefresh = {
      projectId,
      promise,
    }
    catalogRefresh = request
    try {
      return await request.promise
    } finally {
      if (catalogRefresh === request) catalogRefresh = null
    }
  }

  function synchronizeSuccessfulRoute(to: RouteLocationNormalized): void {
    const projectId = projectIdFromRoute(to)
    if (projectId !== null) projects.setActiveProjectId(projectId)
    const navigationId = to.path.startsWith('/projects/')
      ? projectId
      : projects.activeProjectId
    if (navigationId !== null) void refreshNavigationCatalog(navigationId)
  }

  onMounted(() => synchronizeSuccessfulRoute(route))
  const removeAfterEach = router.afterEach((to, _from, failure) => {
    if (!failure) synchronizeSuccessfulRoute(to)
  })
  onBeforeUnmount(removeAfterEach)

  async function selectProject(projectId: number): Promise<boolean> {
    const destination = projectSwitchDestination(route, projectId)
    if (destination) {
      try {
        const failure = await router.push(destination)
        return !isNavigationFailure(failure)
      } catch {
        return false
      }
    }

    projects.setActiveProjectId(projectId)
    await refreshNavigationCatalog(projectId)
    return true
  }

  return {
    projectItems,
    navigationProjectId,
    currentProject,
    projectNavSections,
    routeViewKey,
    selectProject,
  }
}
