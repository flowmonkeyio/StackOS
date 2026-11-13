import { onBeforeUnmount, ref, type Ref } from 'vue'
import type {
  SchemaAccountOut,
  SchemaAuthStatusOut,
} from '@/api'
import { apiFetch, formatApiError } from '@/lib/client'
import { useProjectsStore, type Project } from '@/stores/projects'

export function useProjectOverview(projectId: Ref<number>) {
  const projects = useProjectsStore()
  const project = ref<Project | null>(null)
  const accounts = ref<SchemaAccountOut[]>([])
  const loading = ref(true)
  const errors = ref<Record<string, string>>({})
  let generation = 0
  onBeforeUnmount(() => {
    generation += 1
  })

  async function load(): Promise<void> {
    const request = ++generation
    const id = projectId.value
    async function read<T>(key: string, promise: Promise<T>, commit: (data: T) => void) {
      try {
        const data = await promise
        if (request !== generation || projectId.value !== id) return
        commit(data)
        const next = { ...errors.value }
        delete next[key]
        errors.value = next
      } catch (err) {
        if (request === generation)
          errors.value = {
            ...errors.value,
            [key]: formatApiError(err, `Could not refresh ${key}.`),
          }
      }
    }
    await Promise.all([
      read(
        'connections',
        apiFetch<SchemaAuthStatusOut>(`/api/v1/projects/${id}/connections/accounts?summary_only=true`),
        (data) => {
          accounts.value = data.accounts
        },
      ),
      read(
        'work',
        projects.getById(id) ? Promise.resolve(projects.getById(id)!) : apiFetch<Project>(`/api/v1/projects/${id}`),
        (data) => {
          if (data.id !== id) throw new Error('Project metadata scope did not match this Home.')
          projects.reconcileProjectMetadata([data])
          project.value = projects.isProjectDeleted(id) ? null : data
        },
      ),
    ])
    if (request === generation) loading.value = false
  }
  return { project, accounts, loading, errors, load }
}
