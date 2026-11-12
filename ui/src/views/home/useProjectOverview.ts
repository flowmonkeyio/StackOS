import { onBeforeUnmount, ref, type Ref } from 'vue'
import type {
  SchemaAccountOut,
  SchemaAuthStatusOut,
} from '@/api'
import { apiFetch, formatApiError } from '@/lib/client'
import { callOperation } from '@/lib/operations'
import { useProjectsStore } from '@/stores/projects'
import {
  type OverviewPage,
  type PortfolioProject,
} from './ticketOverview'

export function useProjectOverview(projectId: Ref<number>) {
  const projects = useProjectsStore()
  const project = ref<PortfolioProject | null>(null)
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
        if (request !== generation) return
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
        apiFetch<SchemaAuthStatusOut>(`/api/v1/projects/${id}/connections/accounts`),
        (data) => {
          accounts.value = data.accounts
        },
      ),
      read(
        'work',
        callOperation<OverviewPage<PortfolioProject>>('project.portfolio', {
          project_id: id,
          is_active: null,
          limit: 1,
        }),
        (page) => {
          projects.reconcileProjectMetadata(page.items)
          project.value = projects.isProjectDeleted(id) ? null : page.items[0] ?? null
        },
      ),
    ])
    if (request === generation) loading.value = false
  }
  return { project, accounts, loading, errors, load }
}
