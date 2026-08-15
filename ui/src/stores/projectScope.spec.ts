import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { useProjectDataStore } from './projectData'
import { useStackOsResourcesStore } from './stackosResources'
import { useWorkflowTemplatesStore } from './workflowTemplates'

const clientMocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
}))

vi.mock('@/lib/client', () => ({
  apiFetch: clientMocks.apiFetch,
  formatApiError: (error: unknown, fallback: string) =>
    error instanceof Error ? error.message : fallback,
}))

describe('project-scoped stores', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    clientMocks.apiFetch.mockReset()
  })

  it('keeps newer project data when an older aggregate request finishes last', async () => {
    const projectOne = deferred<void>()
    clientMocks.apiFetch.mockImplementation(async (url: string) => {
      const projectId = url.includes('/projects/1/') ? 1 : 2
      if (projectId === 1) await projectOne.promise
      return { items: [{ id: projectId }] }
    })
    const store = useProjectDataStore()

    const older = store.refresh(1)
    await store.refresh(2)
    projectOne.resolve()
    await older

    expect(store.currentProjectId).toBe(2)
    expect(store.timeline.map((row) => row.id)).toEqual([2])
    expect(store.error).toBeNull()
  })

  it('keeps resource schemas, records, and artifacts in one project scope', async () => {
    const projectOne = deferred<void>()
    clientMocks.apiFetch.mockImplementation(async (url: string) => {
      const projectId = url.includes('project_id=1') || url.includes('/projects/1/') ? 1 : 2
      if (projectId === 1) await projectOne.promise
      if (url.startsWith('/api/v1/resources')) {
        return [{ id: projectId, key: `resource-${projectId}` }]
      }
      return { items: [{ id: projectId }] }
    })
    const store = useStackOsResourcesStore()

    const older = store.refresh(1)
    await store.refresh(2)
    projectOne.resolve()
    await older

    expect(store.currentProjectId).toBe(2)
    expect(store.resources.map((row) => row.id)).toEqual([2])
    expect(store.records.map((row) => row.id)).toEqual([2])
    expect(store.artifacts.map((row) => row.id)).toEqual([2])
  })

  it('does not let an old workflow-template failure replace the new scope', async () => {
    const projectOne = deferred<void>()
    clientMocks.apiFetch.mockImplementation(async (url: string) => {
      if (url.includes('/projects/1/')) {
        await projectOne.promise
        throw new Error('project one unavailable')
      }
      return { templates: [] }
    })
    const store = useWorkflowTemplatesStore()

    const older = store.refresh(1)
    await store.refresh(2)
    projectOne.resolve()
    await older

    expect(store.currentProjectId).toBe(2)
    expect(store.items).toEqual([])
    expect(store.error).toBeNull()
  })
})

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  const promise = new Promise<T>((next) => {
    resolve = next
  })
  return { promise, resolve }
}
