// Projects store — project list and local navigation selection.

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { apiFetch } from '@/lib/client'
import { callOperation } from '@/lib/operations'
import { parseStackOsDate } from '@/lib/stackos/time'
import { changesOrder, type ReorderMove } from '@/components/ui/verticalReorder'
import type { SchemaProjectCreateRequest, SchemaWriteEnvelopeProjectCleanupResult, SchemaWriteResponseProjectOut, components } from '@/api'

export type Project = components['schemas']['ProjectOut']
export type ProjectCleanupResult = components['schemas']['ProjectCleanupResult']
type ProjectsPage = components['schemas']['PageResponse_ProjectOut_']
type ProjectMetadata = Pick<Project, 'id' | 'name' | 'domain' | 'is_active' | 'updated_at'>

const DEFAULT_LIMIT = 50

function isNewerMetadata(incoming: string, current: string): boolean {
  const nextTime = parseStackOsDate(incoming).getTime()
  const currentTime = parseStackOsDate(current).getTime()
  if (!Number.isFinite(nextTime)) return false
  if (!Number.isFinite(currentTime)) return true
  if (nextTime !== currentTime) return nextTime > currentTime
  // Project timestamps retain Python's microseconds; Date only compares milliseconds.
  const subMillisecond = (value: string) => (value.match(/\.(\d+)/)?.[1] ?? '').padEnd(6, '0').slice(3, 6)
  return subMillisecond(incoming) > subMillisecond(current)
}

function preserveNewerMetadata(incoming: Project, current?: Project): Project {
  if (!current || !isNewerMetadata(current.updated_at, incoming.updated_at)) return incoming
  return { ...incoming, name: current.name, domain: current.domain, is_active: current.is_active, updated_at: current.updated_at }
}

export const useProjectsStore = defineStore('projects', () => {
  const items = ref<Project[]>([])
  const totalEstimate = ref<number>(0)
  const nextCursor = ref<number | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const activeProjectId = ref<number | null>(null)
  const catalogComplete = ref(false)
  const managementPending = ref(false)
  // Session-local exclusion of confirmed missing IDs fences reads that began
  // before cleanup. IDs are never reused; this stores no deleted project data.
  const removedIds = ref(new Set<number>())
  const incompleteCleanups = new Map<number, Project['cleanup_json']>()
  let refreshRequest: { key: string; promise: Promise<boolean> } | null = null

  function isProjectDeleted(id: number): boolean { return removedIds.value.has(id) }

  const activeProject = computed<Project | null>(() => {
    if (activeProjectId.value === null) return null
    return items.value.find((project) => project.id === activeProjectId.value) ?? null
  })

  function _ingestPage(page: ProjectsPage, append: boolean): void {
    const rows = (append ? [...items.value, ...page.items] : page.items).filter((project) => !isProjectDeleted(project.id))
    const current = new Map(items.value.map((project) => [project.id, project]))
    items.value = [...new Map(rows.map((project) => {
      const saved = preserveNewerMetadata(project, current.get(project.id))
      const cleanup = incompleteCleanups.get(project.id)
      return [project.id, cleanup ? { ...saved, is_active: false, cleanup_json: cleanup } : saved] as const
    })).values()].sort(
      (left, right) => left.order_index - right.order_index || left.id - right.id,
    )
    nextCursor.value = page.next_cursor ?? null
    totalEstimate.value = Math.max(items.value.length, (page.total_estimate ?? items.value.length)
      - page.items.filter((project) => isProjectDeleted(project.id)).length)
    if (activeProjectId.value === null && !managementPending.value) {
      const firstLiveProject = items.value.find((project) => project.is_active)
      if (firstLiveProject) activeProjectId.value = firstLiveProject.id
    }
  }

  function reconcileProjectMetadata(snapshots: ProjectMetadata[]): void {
    const incoming = new Map(snapshots.map((project) => [project.id, project]))
    // Portfolio rows are partial project records: update only their shared
    // metadata on already loaded IDs, never list membership/order or selection.
    items.value = items.value.map((current) => {
      const next = incoming.get(current.id)
      if (!next || !isNewerMetadata(next.updated_at, current.updated_at)) return current
      return { ...current, name: next.name, domain: next.domain,
        is_active: incompleteCleanups.has(current.id) ? false : next.is_active, updated_at: next.updated_at }
    })
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
      const saved = preserveNewerMetadata(response.data, getById(id) ?? undefined)
      items.value = items.value.map((item) => item.id === id ? saved : item)
      return saved
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

  async function deleteProject(target: Pick<Project, 'id' | 'slug'>): Promise<ProjectCleanupResult> {
    if (managementPending.value) throw new Error('Wait for the current project update.')
    const current = getById(target.id)
    if (current && current.slug !== target.slug) throw new Error('Deletion target no longer matches.')
    managementPending.value = true
    try {
      const response = await callOperation<SchemaWriteEnvelopeProjectCleanupResult>('project.delete', {
        project_id: target.id, scope: 'catalog',
        confirmation: { permanent: true, project_id: target.id, project_slug: target.slug },
      })
      const result = response.data
      if (result.project_id !== target.id) throw new Error('Could not confirm the deletion target.')
      if (result.status === 'deleted' || result.status === 'missing') {
        removedIds.value.add(target.id)
        incompleteCleanups.delete(target.id)
        const wasLoaded = items.value.some((project) => project.id === target.id)
        items.value = items.value.filter((project) => project.id !== target.id)
        if (wasLoaded) totalEstimate.value = Math.max(items.value.length, totalEstimate.value - 1)
        // Selection/routing belongs to useProjectNavigation, including failures.
      } else if (result.status === 'incomplete') {
        const cleanup = { status: 'incomplete', operation_id: result.operation_id, error_code: result.error_code,
          ownership_required_artifact_ids: result.ownership_required_artifact_ids }
        incompleteCleanups.set(target.id, cleanup)
        items.value = items.value.map((project) => project.id === target.id ? {
          ...project, is_active: false, cleanup_json: cleanup,
        } : project)
      }
      return result
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
    deleteProject,
    isProjectDeleted,
    reconcileProjectMetadata,
    moveProject,
    createProject,
    setActiveProjectId,
    getById,
  }
})
