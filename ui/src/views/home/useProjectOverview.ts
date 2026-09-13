import { onBeforeUnmount, ref, type Ref } from 'vue'
import type {
  SchemaAccountOut,
  SchemaAuthStatusOut,
  SchemaWorkflowTemplateExtensionListOut,
  SchemaWorkflowTemplateListOut,
} from '@/api'
import { apiFetch, formatApiError } from '@/lib/client'
import { callOperation } from '@/lib/operations'
import {
  configuredWorkflows,
  type ConfiguredWorkflow,
  type OverviewPage,
  type PortfolioProject,
  type RecordedTask,
} from './ticketOverview'

export function useProjectOverview(projectId: Ref<number>) {
  const project = ref<PortfolioProject | null>(null)
  const accounts = ref<SchemaAccountOut[]>([])
  const workflows = ref<ConfiguredWorkflow[]>([])
  const task = ref<RecordedTask | null>(null)
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
        'workflows',
        Promise.all([
          callOperation<SchemaWorkflowTemplateExtensionListOut>('workflowExtension.list', {
            project_id: id,
          }),
          apiFetch<SchemaWorkflowTemplateListOut>(`/api/v1/projects/${id}/workflow-templates`),
        ]),
        ([extensions, catalog]) => {
          workflows.value = configuredWorkflows(extensions.extensions, catalog.templates)
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
          project.value = page.items[0] ?? null
          task.value = project.value?.latest_task ?? null
        },
      ),
    ])
    if (request === generation) loading.value = false
  }
  return { project, accounts, workflows, task, loading, errors, load }
}
