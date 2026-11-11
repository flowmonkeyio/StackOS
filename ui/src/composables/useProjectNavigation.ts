import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
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
import { useProjectsStore, type Project } from '@/stores/projects'
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
  let selectionIntent = 0
  const renameTargetId = ref<number | null>(null)
  const deleteTarget = ref<Project | null>(null)
  const renameProjectTarget = computed(() => renameTargetId.value === null ? null : projects.getById(renameTargetId.value))
  // Keep exact identity through uncertain cleanup and navigation failure, even
  // after a catalog read removes the row. Shared metadata supplies current names.
  const deleteProjectTarget = computed(() => deleteTarget.value === null ? null
    : projects.getById(deleteTarget.value.id) ?? deleteTarget.value)
  function requestProjectRename(id: number): void {
    if (projects.getById(id)) renameTargetId.value = id
  }
  function requestProjectDelete(id: number): void {
    const project = projects.getById(id)
    if (project) deleteTarget.value = { ...project }
  }
  function closeProjectRename(): void { renameTargetId.value = null }
  function closeProjectDelete(): void { deleteTarget.value = null }

  const { routeProjectId, isProjectRoute, scopeKey: routeViewKey } =
    useProjectRouteScope(route)
  const navigationProjectId = computed(() => {
    const id = isProjectRoute.value ? routeProjectId.value : activeProject.value?.id ?? null
    return id !== null && projects.isProjectDeleted(id) ? null : id
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
    if (projectId !== null && !projects.isProjectDeleted(projectId)) projects.setActiveProjectId(projectId)
    const navigationId = to.path.startsWith('/projects/')
      ? projectId
      : projects.activeProjectId
    if (navigationId !== null && !projects.isProjectDeleted(navigationId)) void refreshNavigationCatalog(navigationId)
  }

  onMounted(() => synchronizeSuccessfulRoute(route))
  const removeAfterEach = router.afterEach((to, _from, failure) => {
    if (!failure) synchronizeSuccessfulRoute(to)
  })
  // Resolve after the existing asynchronous leave/update guards, before the
  // router commits. Suppressing store synchronization after commit is too late.
  const removeBeforeResolve = router.beforeResolve((to) => {
    const projectId = projectIdFromRoute(to)
    return projectId === null || !projects.isProjectDeleted(projectId)
  })
  onBeforeUnmount(() => {
    selectionIntent++
    removeBeforeResolve()
    removeAfterEach()
  })

  async function selectProject(projectId: number): Promise<boolean> {
    if (projects.isProjectDeleted(projectId)) return false
    selectionIntent++
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

  async function finishProjectDeletion(projectId: number): Promise<boolean> {
    if (!projects.isProjectDeleted(projectId)) return false
    // Inspect the current route/selection after cleanup: a later user choice
    // must survive a delayed deletion response.
    if (routeProjectId.value !== projectId && projects.activeProjectId !== projectId) return true
    const intent = selectionIntent
    const fromPath = route.fullPath
    const fromSelection = projects.activeProjectId
    const loaded = projects.catalogComplete || await projects.loadAll()
    if (selectionIntent !== intent || route.fullPath !== fromPath || projects.activeProjectId !== fromSelection) {
      // A newer choice owns navigation, including one still waiting on a
      // leave guard. Keep retry available if its destination has not committed.
      const currentId = routeProjectId.value
      return currentId === null || !projects.isProjectDeleted(currentId)
    }
    if (!loaded) return false
    const survivor = projects.items.find((project) => project.is_active && !project.cleanup_json)
      ?? projects.items.find((project) => !project.cleanup_json)
    if (survivor) return selectProject(survivor.id)
    try {
      const failure = await router.push('/')
      if (isNavigationFailure(failure) && route.path !== '/') return false
      if (selectionIntent === intent) projects.setActiveProjectId(null)
      return true
    } catch { return false }
  }

  async function viewProjectWork(projectId: number): Promise<boolean> {
    selectionIntent++
    try {
      const failure = await router.push({ path: `/projects/${projectId}/tasks` })
      return !isNavigationFailure(failure) || route.path === `/projects/${projectId}/tasks`
    } catch { return false }
  }

  return {
    projectItems,
    navigationProjectId,
    currentProject,
    projectNavSections,
    routeViewKey,
    selectProject,
    projectManagementLoading: computed(() => projects.loading),
    projectManagementPending: computed(() => projects.managementPending),
    projectOrderReady: computed(() => projects.catalogComplete),
    projectManagementError: computed(() => projects.error),
    loadProjectCatalog: projects.loadAll,
    moveProject: projects.moveProject,
    renameProjectTarget,
    deleteProjectTarget,
    requestProjectRename,
    requestProjectDelete,
    closeProjectRename,
    closeProjectDelete,
    finishProjectDeletion,
    viewProjectWork,
  }
}
