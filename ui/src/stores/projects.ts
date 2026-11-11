// Projects store — project list and local navigation selection.

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { apiFetch } from '@/lib/client'
import { callOperation } from '@/lib/operations'
import { changesOrder, type ReorderMove } from '@/components/ui/verticalReorder'
import type { SchemaProjectCreateRequest, SchemaWriteResponseProjectOut, components } from '@/api'

export type Project = components['schemas']['ProjectOut']
type ProjectsPage = components['schemas']['PageResponse_ProjectOut_']

const DEFAULT_LIMIT = 50

export const useProjectsStore = defineStore('projects', () => {
  const items = ref<Project[]>([])
  const totalEstimate = ref<number>(0)
  const nextCursor = ref<number | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const activeProjectId = ref<number | null>(null)
  const catalogComplete = ref(false)
  const managementPending = ref(false)
  let refreshRequest: { key: string; promise: Promise<boolean> } | null = null

  const activeProject = computed<Project | null>(() => {
    if (activeProjectId.value === null) return null
    return items.value.find((project) => project.id === activeProjectId.value) ?? null
  })

  function _ingestPage(page: ProjectsPage, append: boolean): void {
    const rows = append ? [...items.value, ...page.items] : page.items
    items.value = [...new Map(rows.map((project) => [project.id, project])).values()].sort(
      (left, right) => left.order_index - right.order_index || left.id - right.id,
    )
    nextCursor.value = page.next_cursor ?? null
    totalEstimate.value = page.total_estimate ?? items.value.length
    if (activeProjectId.value === null && !managementPending.value) {
      const firstLiveProject = items.value.find((project) => project.is_active)
      if (firstLiveProject) activeProjectId.value = firstLiveProject.id
    }
  }

  async function readCatalog(activeOnly = false, allPages = false): Promise<boolean> {
    const key = `${activeOnly}:${allPages}`
    if (refreshRequest?.key === key) return refreshRequest.promise
    if (refreshRequest) {
      await refreshRequest.promise
      return readCatalog(activeOnly, allPages)
    }
    // Creation owns its loading interval too; never overlap that write with a
    // catalog replacement that could discard its just-created row.
    if (loading.value) return false

    const request: { key: string; promise: Promise<boolean> } = {
      key,
      promise: Promise.resolve(false),
    }
    const promise = (async () => {
      loading.value = true
      error.value = null
      catalogComplete.value = false
      try {
        const params = new URLSearchParams({ limit: String(DEFAULT_LIMIT) })
        if (activeOnly) params.set('active_only', 'true')
        let page = await apiFetch<ProjectsPage>(`/api/v1/projects?${params.toString()}`)
        const rows = [...page.items]
        const seenCursors = new Set<number>()
        while (allPages && page.next_cursor != null) {
          if (seenCursors.has(page.next_cursor)) throw new Error('Project pagination did not advance. Try again.')
          seenCursors.add(page.next_cursor)
          params.set('after', String(page.next_cursor))
          page = await apiFetch<ProjectsPage>(`/api/v1/projects?${params.toString()}`)
          rows.push(...page.items)
        }
        // Publish complete reads atomically; a failed later page must not
        // replace an already displayed catalog with a misleading first page.
        page = { ...page, items: rows }
        _ingestPage(page, false)
        catalogComplete.value = !activeOnly && page.next_cursor == null
        return true
      } catch (err) {
        error.value = err instanceof Error ? err.message : 'failed to load projects'
        return false
      } finally {
        if (refreshRequest === request) refreshRequest = null
        loading.value = false
      }
    })()
    request.promise = promise
    refreshRequest = request
    return promise
  }

  async function refresh(activeOnly = false): Promise<void> {
    if (!managementPending.value) await readCatalog(activeOnly)
  }

  async function loadAll(): Promise<boolean> {
    if (managementPending.value) return false
    return readCatalog(false, true)
  }

  async function loadMore(): Promise<void> {
    if (nextCursor.value === null || loading.value || managementPending.value) return
    const request = { key: `more:${nextCursor.value}`, promise: Promise.resolve(false) }
    request.promise = (async () => {
      loading.value = true
      error.value = null
      try {
        const params = new URLSearchParams({ limit: String(DEFAULT_LIMIT), after: String(nextCursor.value) })
        const page = await apiFetch<ProjectsPage>(`/api/v1/projects?${params.toString()}`)
        _ingestPage(page, true)
        catalogComplete.value = page.next_cursor == null
        return true
      } catch (err) {
        error.value = err instanceof Error ? err.message : 'failed to load more projects'
        return false
      } finally {
        if (refreshRequest === request) refreshRequest = null
        loading.value = false
      }
    })()
    refreshRequest = request
    await request.promise
  }

  async function renameProject(id: number, draft: string): Promise<Project> {
    const name = draft.trim()
    if (!name) throw new Error('Enter a project name.')
    if (Array.from(name).length > 200) throw new Error('Use 200 characters or fewer.')
    const project = getById(id)
    if (!project) throw new Error('Project is no longer available.')
    if (managementPending.value || loading.value) throw new Error('Wait for the current project update.')
    if (name === project.name) return project
    managementPending.value = true
    try {
      const response = await callOperation<SchemaWriteResponseProjectOut>('project.update', {
        project_id: id, patch: { name }, scope: 'catalog',
      })
      items.value = items.value.map((item) => item.id === id ? response.data : item)
      return response.data
    } catch (err) {
      // A lost response may still have applied the name. Confirm only an
      // authoritative match; genuine failures keep the owning dialog's draft.
      const reconciled = await readCatalog(false, true)
      const current = getById(id)
      if (reconciled && current?.name === name) return current
      throw err
    } finally {
      managementPending.value = false
    }
  }

  async function moveProject(move: ReorderMove): Promise<boolean> {
    const live = items.value.filter((project) => project.is_active)
    if (!catalogComplete.value || loading.value || managementPending.value
      || !changesOrder(live.map((project) => String(project.id)), move)) return false
    managementPending.value = true
    let failure: string | null = null
    try {
      await callOperation<SchemaWriteResponseProjectOut>('project.move', {
        project_id: Number(move.itemKey), anchor_id: Number(move.anchorKey),
        placement: move.placement, scope: 'catalog',
      })
    } catch (err) {
      failure = err instanceof Error ? err.message : 'Could not confirm project order.'
    }
    try {
      // Stored keys for neighbors can change too. Always reconcile every page,
      // including after a rejection or an uncertain transport outcome.
      const reconciled = await readCatalog(false, true)
      if (failure) error.value = [failure, error.value].filter(Boolean).join(' ')
      return !failure && reconciled
    } finally {
      managementPending.value = false
    }
  }

  async function createProject(body: SchemaProjectCreateRequest): Promise<Project> {
    if (managementPending.value || loading.value) throw new Error('Wait for the current project update.')
    loading.value = true
    error.value = null
    try {
      const response = await apiFetch<SchemaWriteResponseProjectOut>('/api/v1/projects', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      items.value = [
        ...items.value.filter((project) => project.id !== response.data.id),
        response.data,
      ]
      totalEstimate.value = Math.max(totalEstimate.value + 1, items.value.length)
      activeProjectId.value = response.data.id
      return response.data
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'failed to create project'
      throw err
    } finally {
      loading.value = false
    }
  }

  function setActiveProjectId(id: number | null): void {
    activeProjectId.value = id
  }

  function getById(id: number): Project | null {
    return items.value.find((project) => project.id === id) ?? null
  }

  return {
    items,
    totalEstimate,
    nextCursor,
    loading,
    error,
    activeProjectId,
    activeProject,
    catalogComplete,
    managementPending,
    refresh,
    loadMore,
    loadAll,
    renameProject,
    moveProject,
    createProject,
    setActiveProjectId,
    getById,
  }
})
